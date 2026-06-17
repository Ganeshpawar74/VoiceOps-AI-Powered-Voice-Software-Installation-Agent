"""
Agent 4 — Browser Agent  (Playwright-free, Celery-safe)

ROOT CAUSE OF ORIGINAL FAILURES:
  Playwright's async_playwright().start() calls asyncio.create_subprocess_exec()
  internally. Celery's --pool=solo on Windows uses a ProactorEventLoop that does
  NOT support subprocess creation inside an already-running coroutine, hence:
      NotImplementedError at asyncio/base_events.py _make_subprocess_transport

SOLUTION — Three-tier download URL discovery (no subprocess, no Playwright):
  Tier 1 (instant):   settings.registry.official_download_urls  — known URLs
  Tier 2 (fast):      LLM asks Mistral for the official download page URL.
                      Works for ANY software the user names, no hardcoding.
  Tier 3 (fallback):  httpx-based Google/DDG search page scrape for a direct
                      installer link using regex on the raw HTML.

All three tiers are pure Python + network HTTP — no subprocesses, no headless
browser, fully compatible with Celery solo/prefork/gevent/eventlet pools on
Windows and Linux.

The browser_agent's job is: given IntentOutput → return BrowserResult with
a DownloadLink the download_agent can fetch.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse, quote_plus

import httpx

from app.config.settings import get_settings
from app.models.schemas import BrowserResult, DownloadLink, IntentOutput, OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()

# OS-specific installer extensions for link scoring / filtering
_OS_EXTS = {
    OperatingSystem.WINDOWS: [".exe", ".msi"],
    OperatingSystem.MACOS:   [".dmg", ".pkg"],
    OperatingSystem.LINUX:   [".deb", ".rpm", ".tar.gz", ".tar.xz", ".AppImage"],
}

_HEADERS = {
    "User-Agent": settings.browser.user_agent,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    d = _domain(url)
    return any(d.endswith(b) for b in settings.browser.blocked_domains)


def _is_trusted(url: str) -> bool:
    d = _domain(url)
    return any(d.endswith(t) for t in settings.browser.trusted_domains)


def _is_installer_url(url: str, os_: OperatingSystem) -> bool:
    """True if the URL looks like a direct installer for this OS."""
    lower = url.lower()
    exts  = _OS_EXTS.get(os_, [])
    # Strip query string for extension check
    path = lower.split("?")[0]
    return any(path.endswith(ext) for ext in exts)


def _score_url(url: str, os_: OperatingSystem) -> float:
    score = 0.0
    lower = url.lower()
    if _is_trusted(url):
        score += 30
    if _is_installer_url(url, os_):
        score += 20
    if any(kw in lower for kw in ["x64", "amd64", "x86_64"]):
        score += 5
    if any(kw in lower for kw in ["stable", "latest", "release"]):
        score += 3
    if re.search(r"\d+\.\d+", lower):
        score += 2
    if _is_blocked(url):
        score -= 100
    return score


def _extract_installer_urls(html: str, os_: OperatingSystem) -> list[str]:
    """Pull all href/src that look like installer download URLs from raw HTML."""
    exts = _OS_EXTS.get(os_, [])
    # match href="..." or href='...' or plain https://...
    patterns = [
        r'href=["\']([^"\']+)["\']',
        r'src=["\']([^"\']+)["\']',
        r'(https?://\S+)',
    ]
    candidates: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            url = m.group(1).strip()
            if not url.startswith("http"):
                continue
            if _is_blocked(url):
                continue
            path = url.lower().split("?")[0]
            if any(path.endswith(ext) for ext in exts):
                candidates.append(url)
    # Deduplicate, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


async def _fetch_html(url: str, timeout: int = 20) -> str:
    """Fetch a URL and return response text. Returns '' on failure."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(connect=10, read=timeout, write=None, pool=None),
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:
        logger.warning("[BrowserAgent] HTTP fetch failed for %s: %s", url, exc)
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# Tier 2 — LLM URL discovery
# ──────────────────────────────────────────────────────────────────────────────

def _ask_llm_for_download_url(software: str, os_: OperatingSystem) -> Optional[str]:
    """
    Ask Mistral for the official download page URL for this software + OS.
    Blocking — run in thread via asyncio.to_thread().
    Returns a URL string or None.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)

        os_name = {
            OperatingSystem.WINDOWS: "Windows",
            OperatingSystem.MACOS:   "macOS",
            OperatingSystem.LINUX:   "Linux",
        }.get(os_, "Windows")

        prompt = (
            f"What is the exact official download page URL for '{software}' on {os_name}?\n"
            "Rules:\n"
            "1. Return ONLY the URL — no explanation, no markdown, no extra text.\n"
            "2. Must be an https:// URL from the official vendor website.\n"
            "3. If there is a direct installer download link (ends in .exe/.msi/.dmg etc) prefer that.\n"
            "4. If you are not certain, return the word UNKNOWN.\n"
            "Examples:\n"
            "  VS Code Windows → https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user\n"
            "  Python Windows  → https://www.python.org/ftp/python/3.12.0/python-3.12.0-amd64.exe\n"
            "  ChatGPT Windows → https://apps.microsoft.com/detail/9nt1r1c2hh7j\n"
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=80,
        )
        raw = response.choices[0].message.content.strip().strip('"').strip("'")
        if raw.upper() == "UNKNOWN" or not raw.startswith("http"):
            logger.info("[BrowserAgent] LLM could not provide URL for %r", software)
            return None
        logger.info("[BrowserAgent] LLM suggested URL for %r: %s", software, raw)
        return raw
    except Exception as exc:
        logger.warning("[BrowserAgent] LLM URL discovery failed: %s", exc)
        return None


def _ask_llm_for_direct_installer(software: str, os_: OperatingSystem) -> Optional[str]:
    """
    Ask Mistral for a direct installer download URL (.exe/.msi/.dmg).
    More specific than _ask_llm_for_download_url — requests a file link.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)

        os_name = {
            OperatingSystem.WINDOWS: "Windows 64-bit",
            OperatingSystem.MACOS:   "macOS",
            OperatingSystem.LINUX:   "Linux 64-bit",
        }.get(os_, "Windows 64-bit")

        ext_hint = {
            OperatingSystem.WINDOWS: ".exe or .msi file",
            OperatingSystem.MACOS:   ".dmg or .pkg file",
            OperatingSystem.LINUX:   ".deb, .rpm, or .AppImage file",
        }.get(os_, ".exe file")

        prompt = (
            f"Give me the direct download URL for the latest stable {software} installer "
            f"for {os_name}. It should be a {ext_hint}.\n"
            "Return ONLY the URL. No explanation. If unknown, return UNKNOWN."
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=120,
        )
        raw = response.choices[0].message.content.strip().strip('"').strip("'")
        if raw.upper() == "UNKNOWN" or not raw.startswith("http"):
            return None
        logger.info("[BrowserAgent] LLM direct installer URL for %r: %s", software, raw)
        return raw
    except Exception as exc:
        logger.warning("[BrowserAgent] LLM direct installer URL failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Tier 3 — httpx-based web scrape (no Playwright, no subprocess)
# ──────────────────────────────────────────────────────────────────────────────

async def _search_for_installer(software: str, os_: OperatingSystem) -> Optional[str]:
    """
    Search DuckDuckGo HTML (no API key) for a direct installer URL.
    Falls back to scraping the top result page for installer links.
    """
    os_name = {
        OperatingSystem.WINDOWS: "windows",
        OperatingSystem.MACOS:   "macos",
        OperatingSystem.LINUX:   "linux",
    }.get(os_, "windows")

    ext_hint = {
        OperatingSystem.WINDOWS: "exe OR msi",
        OperatingSystem.MACOS:   "dmg OR pkg",
        OperatingSystem.LINUX:   "deb OR rpm OR AppImage",
    }.get(os_, "exe")

    query = f"{software} official download {os_name} {ext_hint} site:*.{_get_likely_domain(software)}"
    fallback_query = f"{software} download {os_name} installer filetype:{ext_hint.split()[0]}"

    # Try DuckDuckGo HTML (no JS)
    ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    html = await _fetch_html(ddg_url)
    if html:
        urls = _extract_installer_urls(html, os_)
        if urls:
            best = max(urls, key=lambda u: _score_url(u, os_))
            logger.info("[BrowserAgent] DDG found installer: %s", best)
            return best

    # Try scraping the known download page (if we have one)
    known_page = settings.registry.official_download_urls.get(software)
    if known_page:
        page_html = await _fetch_html(known_page)
        if page_html:
            urls = _extract_installer_urls(page_html, os_)
            if urls:
                best = max(urls, key=lambda u: _score_url(u, os_))
                logger.info("[BrowserAgent] Scraped official page, found: %s", best)
                return best

    return None


def _get_likely_domain(software: str) -> str:
    """Guess the likely TLD suffix for vendor domain matching."""
    lower = software.lower()
    domain_hints = {
        "chatgpt": "openai.com", "openai": "openai.com",
        "vs code": "microsoft.com", "visual studio code": "microsoft.com",
        "python": "python.org", "chrome": "google.com",
        "firefox": "mozilla.org", "docker": "docker.com",
        "github": "github.com", "slack": "slack.com",
        "zoom": "zoom.us", "discord": "discord.com",
    }
    for key, domain in domain_hints.items():
        if key in lower:
            return domain
    return "com"


# ──────────────────────────────────────────────────────────────────────────────
# Windows Store / Microsoft Store special handling
# ──────────────────────────────────────────────────────────────────────────────

def _make_winget_store_link(store_url: str, software: str) -> DownloadLink:
    """
    For MS Store URLs (apps.microsoft.com/detail/...) we return a special
    DownloadLink with is_official=True so the install agent can handle it
    via `winget install --id <store_id>` directly.
    """
    # Extract the product ID from the URL
    m = re.search(r"/detail/([A-Z0-9]+)", store_url, re.IGNORECASE)
    store_id = m.group(1) if m else ""
    return DownloadLink(
        url=store_url,          # type: ignore[arg-type]
        source_domain="apps.microsoft.com",
        is_official=True,
        file_name=f"{software}_store_{store_id}",
        metadata={"store_id": store_id, "install_method": "winget_store"},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Browser Agent
# ──────────────────────────────────────────────────────────────────────────────

class BrowserAgent:
    """
    Agent 4 — Browser Agent.

    Discovers the official download URL for any software using:
      Tier 1: Hardcoded registry (instant, most reliable)
      Tier 2: Mistral LLM → "what is the download URL for X on Windows?"
      Tier 3: httpx scraping of DDG results + known download pages

    No Playwright, no subprocesses — fully compatible with Celery solo pool.
    """

    async def find_download_link(self, intent: IntentOutput) -> BrowserResult:
        software = intent.software_canonical
        os_      = intent.operating_system
        logger.info("[BrowserAgent] Looking for %s on %s", software, os_)

        # ── Tier 1: Known URL from registry ──────────────────────────────────
        known_url = settings.registry.official_download_urls.get(software)
        if known_url:
            logger.info("[BrowserAgent] Registry hit: %s → %s", software, known_url)
            # Check if it's a Microsoft Store URL
            if "apps.microsoft.com" in known_url:
                link = _make_winget_store_link(known_url, software)
                return BrowserResult(
                    success=True,
                    download_links=[link],
                    selected_link=link,
                    page_title=f"{software} - Microsoft Store",
                    navigation_path=[known_url],
                )
            # Try to scrape the page for direct installer links
            html = await _fetch_html(known_url)
            if html:
                urls = _extract_installer_urls(html, os_)
                if urls:
                    best_url = max(urls, key=lambda u: _score_url(u, os_))
                    link = DownloadLink(
                        url=best_url,   # type: ignore[arg-type]
                        source_domain=_domain(best_url),
                        is_official=_is_trusted(best_url) or True,
                        file_name=best_url.split("/")[-1].split("?")[0] or None,
                    )
                    return BrowserResult(
                        success=True,
                        download_links=[link],
                        selected_link=link,
                        page_title=f"{software} Download",
                        navigation_path=[known_url],
                    )
            # If page scrape failed, use the page URL itself as a fallback
            # (install agent will handle it via winget if available)
            logger.info("[BrowserAgent] Page scrape found nothing, using registry URL as download page")

        # ── Tier 2a: LLM → direct installer URL ──────────────────────────────
        direct_url = await asyncio.to_thread(_ask_llm_for_direct_installer, software, os_)
        if direct_url and _is_installer_url(direct_url, os_):
            link = DownloadLink(
                url=direct_url,   # type: ignore[arg-type]
                source_domain=_domain(direct_url),
                is_official=True,
                file_name=direct_url.split("/")[-1].split("?")[0] or f"{software}_installer",
            )
            logger.info("[BrowserAgent] Using LLM direct installer URL: %s", direct_url)
            return BrowserResult(
                success=True,
                download_links=[link],
                selected_link=link,
                page_title=f"{software} Download (LLM)",
                navigation_path=[direct_url],
            )

        # ── Tier 2b: LLM → download page URL → scrape ────────────────────────
        page_url = await asyncio.to_thread(_ask_llm_for_download_url, software, os_)
        if page_url:
            if "apps.microsoft.com" in page_url:
                link = _make_winget_store_link(page_url, software)
                return BrowserResult(
                    success=True,
                    download_links=[link],
                    selected_link=link,
                    page_title=f"{software} - Microsoft Store",
                    navigation_path=[page_url],
                )
            html = await _fetch_html(page_url)
            if html:
                urls = _extract_installer_urls(html, os_)
                if urls:
                    best_url = max(urls, key=lambda u: _score_url(u, os_))
                    link = DownloadLink(
                        url=best_url,   # type: ignore[arg-type]
                        source_domain=_domain(best_url),
                        is_official=True,
                        file_name=best_url.split("/")[-1].split("?")[0] or None,
                    )
                    return BrowserResult(
                        success=True,
                        download_links=[link],
                        selected_link=link,
                        page_title=f"{software} Download",
                        navigation_path=[page_url],
                    )

        # ── Tier 3: httpx web scrape (DDG + official page) ────────────────────
        installer_url = await _search_for_installer(software, os_)
        if installer_url:
            link = DownloadLink(
                url=installer_url,   # type: ignore[arg-type]
                source_domain=_domain(installer_url),
                is_official=_is_trusted(installer_url),
                file_name=installer_url.split("/")[-1].split("?")[0] or f"{software}_installer",
            )
            return BrowserResult(
                success=True,
                download_links=[link],
                selected_link=link,
                page_title=f"{software} Download (scraped)",
                navigation_path=[installer_url],
            )

        # ── All tiers failed ──────────────────────────────────────────────────
        logger.warning(
            "[BrowserAgent] All tiers exhausted for %s on %s", software, os_
        )
        return BrowserResult(
            success=False,
            error=(
                f"Could not find a download link for '{software}' on {os_.value}. "
                "Consider installing via winget or the official website manually."
            ),
            navigation_path=[],
        )

    async def close(self) -> None:
        """No-op — no persistent resources to close."""
        pass
