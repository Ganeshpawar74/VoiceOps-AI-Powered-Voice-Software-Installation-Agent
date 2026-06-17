"""
Agent 4 — Browser Agent
Opens browser, navigates to official pages, extracts download links.
Strategy: registry-based URLs → Playwright DOM → OCR fallback.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config.settings import get_settings
from app.models.schemas import BrowserResult, DownloadLink, IntentOutput, OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()


# OS filter for link scoring (still needed for _score_link function)
OS_FILTER_MAP = {
    OperatingSystem.WINDOWS: [".exe", ".msi", "win"],
    OperatingSystem.MACOS:   [".dmg", ".pkg", "mac", "darwin"],
    OperatingSystem.LINUX:   [".deb", ".rpm", ".tar.gz", "linux"],
}


def _is_trusted(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower()
        return any(domain.endswith(td) for td in settings.browser.trusted_domains)
    except Exception:
        return False


def _score_link(href: str, os_: OperatingSystem, arch: str = "x64") -> float:
    score  = 0.0
    href_l = href.lower()

    for ext in OS_FILTER_MAP.get(os_, []):
        if ext in href_l:
            score += 10

    for kw in ["x64", "x86_64", "amd64"]:
        if kw in href_l:
            score += 5

    if "arm64" in href_l and arch == "arm64":
        score += 5

    if "stable" in href_l or "latest" in href_l:
        score += 3

    if re.search(r"\d+\.\d+", href_l):
        score += 2

    if _is_trusted(href):
        score += 20

    return score


# ──────────────────────────────────────────────
# Browser Agent
# ──────────────────────────────────────────────

class BrowserAgent:
    """
    Agent 4 — Browser Agent.
    Primary:   Playwright DOM scraping.
    Fallback:  OCR via pytesseract on screenshot.
    """

    def __init__(self) -> None:
        self._browser: Optional[Browser] = None
        self._playwright = None

    async def _get_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.browser.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            logger.info("[BrowserAgent] Chromium launched (headless=%s)", settings.browser.headless)
        return self._browser

    async def find_download_link(self, intent: IntentOutput) -> BrowserResult:
        software = intent.software_canonical
        os_      = intent.operating_system
        logger.info("[BrowserAgent] Looking for %s on %s", software, os_)

        # 1. Known URL (fastest, most reliable)
        known_url = settings.registry.official_download_urls.get(software)
        if known_url:
            result = await self._navigate_and_extract(known_url, os_, software)
            if result.success:
                return result
            logger.warning("[BrowserAgent] Known URL failed — falling back to search")

        # 2. Google search fallback
        search_url = (
            "https://www.google.com/search?q="
            + f"{software.replace(' ', '+')}+official+download+{os_.value}"
        )
        return await self._navigate_and_extract(search_url, os_, software)

    async def _navigate_and_extract(
        self, url: str, os_: OperatingSystem, software: str
    ) -> BrowserResult:
        browser = await self._get_browser()
        ctx: BrowserContext = await browser.new_context(
            user_agent=settings.browser.user_agent,
            viewport={"width": 1280, "height": 800},
        )
        page: Page = await ctx.new_page()

        try:
            await page.goto(
                url,
                timeout=settings.browser.navigation_timeout_ms,
                wait_until="networkidle",
            )
            await page.wait_for_timeout(1500)

            # Dismiss cookie banners
            for sel in settings.selectors.cookie_dismiss_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        await page.wait_for_timeout(300)
                except Exception:
                    pass

            links = await self._extract_links(page, os_, software)

            if not links and settings.features.ocr_fallback:
                logger.info("[BrowserAgent] DOM scraping found nothing — trying OCR fallback")
                links = await self._ocr_fallback(page, os_)

            if not links:
                return BrowserResult(
                    success=False,
                    error="No download links found on page",
                    page_title=await page.title(),
                    navigation_path=[url],
                )

            selected = max(links, key=lambda lnk: _score_link(str(lnk.url), os_))

            return BrowserResult(
                success=True,
                download_links=links,
                selected_link=selected,
                page_title=await page.title(),
                navigation_path=[url],
            )

        except Exception as exc:
            logger.error("[BrowserAgent] Navigation error: %s", exc)
            return BrowserResult(success=False, error=str(exc), navigation_path=[url])
        finally:
            await ctx.close()

    async def _extract_links(
        self, page: Page, os_: OperatingSystem, software: str
    ) -> list[DownloadLink]:
        links: list[DownloadLink] = []

        for selector in settings.selectors.download_button_selectors:
            try:
                elements = await page.locator(selector).all()
                for el in elements[:20]:
                    href = await el.get_attribute("href") or ""
                    if not href or not href.startswith("http"):
                        continue
                    if not _is_trusted(href):
                        continue
                    parsed = urlparse(href)
                    links.append(DownloadLink(
                        url=href,                          # type: ignore[arg-type]
                        source_domain=parsed.netloc,
                        is_official=_is_trusted(href),
                        file_name=href.split("/")[-1].split("?")[0] or None,
                    ))
            except Exception:
                continue

        # Dedup by URL
        seen: set[str] = set()
        unique: list[DownloadLink] = []
        for lnk in links:
            k = str(lnk.url)
            if k not in seen:
                seen.add(k)
                unique.append(lnk)
        return unique

    async def _ocr_fallback(
        self, page: Page, os_: OperatingSystem
    ) -> list[DownloadLink]:
        """Screenshot → OCR → regex for installer URLs."""
        try:
            import io
            import pytesseract
            from PIL import Image

            screenshot = await page.screenshot(full_page=True)
            img  = Image.open(io.BytesIO(screenshot))
            text = pytesseract.image_to_string(img)
            urls = re.findall(
                r"https?://\S+\.(?:exe|msi|dmg|pkg|deb|rpm|tar\.gz)", text
            )
            links = []
            for url in urls:
                if _is_trusted(url):
                    links.append(DownloadLink(
                        url=url,                           # type: ignore[arg-type]
                        source_domain=urlparse(url).netloc,
                        is_official=True,
                        file_name=url.split("/")[-1],
                    ))
            return links
        except ImportError:
            logger.warning("[BrowserAgent] pytesseract / Pillow not installed — OCR unavailable")
            return []
        except Exception as exc:
            logger.warning("[BrowserAgent] OCR fallback failed: %s", exc)
            return []

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()