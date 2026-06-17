"""
Agent 5 — Download Agent
Downloads installer, verifies SHA-256 checksum, validates digital signature.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from app.config.settings import get_settings
from app.models.schemas import DownloadLink, DownloadResult, VerificationResult

logger   = logging.getLogger(__name__)
settings = get_settings()

CHUNK_SIZE = 1024 * 1024   # 1 MB


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

async def _stream_download(
    url: str, dest: Path, progress_cb: Optional[Callable[[float], None]] = None
) -> tuple[int, float]:
    """Stream-download url → dest. Returns (bytes_written, duration_sec)."""
    t0      = time.perf_counter()
    written = 0

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=10, read=600, write=None, pool=None),
        headers={"User-Agent": settings.browser.user_agent},
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                    f.write(chunk)
                    written += len(chunk)
                    if progress_cb and total:
                        progress_cb(written / total)

    return written, time.perf_counter() - t0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


# ──────────────────────────────────────────────
# Platform-specific signature verification
# ──────────────────────────────────────────────

def _verify_windows_signature(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature '{path}').Status -eq 'Valid'"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip().lower() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _verify_macos_signature(path: Path) -> bool:
    try:
        cmd = (
            ["pkgutil", "--check-signature", str(path)]
            if path.suffix.lower() == ".pkg"
            else ["codesign", "--verify", "--deep", "--strict", str(path)]
        )
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _verify_linux_signature(path: Path) -> bool:
    sig = Path(str(path) + ".sig")
    if not sig.exists():
        return False
    try:
        result = subprocess.run(
            ["gpg", "--verify", str(sig), str(path)],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


async def _verify_publisher(path: Path) -> bool:
    import platform
    sys_ = platform.system().lower()
    if sys_ == "windows":
        return await asyncio.to_thread(_verify_windows_signature, path)
    if sys_ == "darwin":
        return await asyncio.to_thread(_verify_macos_signature, path)
    if sys_ == "linux":
        return await asyncio.to_thread(_verify_linux_signature, path)
    return False


# ──────────────────────────────────────────────
# Download Agent
# ──────────────────────────────────────────────

class DownloadAgent:
    """Agent 5 — Download Agent."""

    async def download_and_verify(
        self,
        link: DownloadLink,
        expected_sha256: Optional[str] = None,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> DownloadResult:
        url       = str(link.url)
        file_name = link.file_name or url.split("/")[-1].split("?")[0] or "installer"
        # Save to Desktop if feature flag is enabled, otherwise use temp downloads dir
        save_dir  = Path(settings.desktop_dir) if settings.features.download_to_desktop else Path(settings.downloads_dir)
        dest      = save_dir / file_name

        logger.info("[DownloadAgent] %s → %s", url, dest)

        # ── Security pre-checks ──
        if settings.security.require_https and not url.startswith("https://"):
            return DownloadResult(
                success=False,
                error="Rejected: URL must use HTTPS",
                verification=VerificationResult.FAILED,
            )

        if not link.is_official:
            return DownloadResult(
                success=False,
                error="Rejected: source domain not in trusted list",
                verification=VerificationResult.FAILED,
            )

        # ── Download ──
        try:
            written, duration = await _stream_download(url, dest, progress_cb)
        except httpx.HTTPStatusError as exc:
            return DownloadResult(
                success=False,
                error=f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
            )
        except Exception as exc:
            logger.error("[DownloadAgent] Download failed: %s", exc)
            return DownloadResult(success=False, error=str(exc))

        size_mb = written / (1024 * 1024)
        if size_mb > settings.security.max_file_size_mb:
            dest.unlink(missing_ok=True)
            return DownloadResult(
                success=False,
                error=f"File too large: {size_mb:.0f} MB exceeds {settings.security.max_file_size_mb} MB limit",
            )

        # ── Checksum ──
        actual_sha   = _sha256(dest)
        verification = VerificationResult.SKIPPED

        if settings.security.verify_checksums and expected_sha256:
            verification = (
                VerificationResult.VERIFIED
                if actual_sha.lower() == expected_sha256.lower()
                else VerificationResult.FAILED
            )
            if verification == VerificationResult.FAILED:
                dest.unlink(missing_ok=True)
                return DownloadResult(
                    success=False,
                    error="SHA-256 checksum mismatch — possible file tampering",
                    verification=VerificationResult.FAILED,
                )

        # ── Publisher signature ──
        publisher_ok = False
        if settings.security.verify_publisher:
            publisher_ok = await _verify_publisher(dest)

        logger.info(
            "[DownloadAgent] OK: %s (%.1f MB, %.1fs, sha256=%s…, sig=%s)",
            file_name, size_mb, duration, actual_sha[:12], publisher_ok,
        )

        return DownloadResult(
            success=True,
            local_path=str(dest),
            file_name=file_name,
            file_size_bytes=written,
            download_duration_sec=round(duration, 2),
            checksum_sha256=actual_sha,
            verification=verification,
            publisher_verified=publisher_ok,
        )