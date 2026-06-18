"""
Agent 6 — Install Agent  (REWRITTEN)

"""

from __future__ import annotations

import asyncio
import glob as _glob
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.config.settings import get_settings
from app.models.schemas import (
    DownloadResult,
    InstallResult,
    OperatingSystem,
)

logger   = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Winget exit code helpers
# ---------------------------------------------------------------------------

def _u32(n: int) -> int:
    """Convert signed int32 to unsigned uint32 for Windows HRESULT comparison."""
    return n & 0xFFFFFFFF


_WINGET_EXIT_CODES: dict[int, str] = {
    0:                        "Success",
    3010:                     "Success, reboot required",
    1638:                     "Another version already installed",
    1603:                     "Fatal error during installation",
    1618:                     "Another installation already in progress",
    2:                        "File not found",
    740:                      "Elevation required (run as admin)",
    _u32(-1978335189):        "Already installed / no upgrade needed (0x8A15002B)",
    -1978335189:              "Already installed / no upgrade needed (0x8A15002B)",
    _u32(-1978335215):        "No applicable upgrade found (0x8A15001F)",
    -1978335215:              "No applicable upgrade found (0x8A15001F)",
    _u32(-1978334967):        "Installer hash mismatch (0x8A150109)",
    -1978334967:              "Installer hash mismatch (0x8A150109)",
    _u32(-1978335188):        "Package not found (0x8A15002C)",
    -1978335188:              "Package not found (0x8A15002C)",
    _u32(-1978335212):        "Installation in progress elsewhere (0x8A150014)",
    -1978335212:              "Installation in progress elsewhere (0x8A150014)",
}

_WINGET_SUCCESS_CODES = {
    0,
    3010,
    1638,
    _u32(-1978335189),
    -1978335189,
    _u32(-1978335215),
    -1978335215,
}

TIMEOUT_SEC = 300   # 5 min max per install


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_winget() -> Optional[str]:
    """Locate winget.exe — checks PATH and AppInstaller location."""
    path = shutil.which("winget")
    if path:
        return path
    appinstaller = (
        Path.home()
        / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "winget.exe"
    )
    if appinstaller.exists():
        return str(appinstaller)
    return None


def _extract_version(stdout: str) -> Optional[str]:
    """Pull installed version from winget stdout."""
    m = re.search(r"version\s+([\d.]+)", stdout, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"([\d]+\.[\d]+\.[\d.]+)", stdout)
    if m:
        return m.group(1)
    return None


def _run_winget(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """Synchronous winget install call — run inside asyncio.to_thread()."""
    winget = _find_winget()
    if not winget:
        return False, "winget not found. Install App Installer from the Microsoft Store.", [], None

    cmd = [
        winget, "install",
        "--id", package_id,
        "--silent",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--disable-interactivity",
    ]
    logs: list[str] = [f"Running: {' '.join(cmd)}"]
    logger.info("[InstallAgent] winget cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"winget timed out after {TIMEOUT_SEC}s", logs, None
    except FileNotFoundError:
        return False, "winget.exe not found — is App Installer installed?", logs, None

    logs.append(f"stdout: {proc.stdout[:2000]}")
    if proc.stderr:
        logs.append(f"stderr: {proc.stderr[:500]}")

    code = proc.returncode
    code_msg = _WINGET_EXIT_CODES.get(code, f"exit code {code}")
    logs.append(f"exit: {code} ({code_msg})")

    success = code in _WINGET_SUCCESS_CODES

    stdout_lower = proc.stdout.lower()
    if not success and any(phrase in stdout_lower for phrase in [
        "already installed", "no available upgrade", "no newer package"
    ]):
        success = True
        logs.append("Treating as success: software already present on system")

    version = _extract_version(proc.stdout) if success else None
    error   = None if success else f"winget failed: {code_msg}\n{proc.stderr[:300]}"
    return success, error or "", logs, version


def _run_winget_store(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """Install a Microsoft Store app via winget using --source msstore."""
    winget = _find_winget()
    if not winget:
        return False, "winget not found. Install App Installer from the Microsoft Store.", [], None

    cmd = [
        winget, "install",
        "--id", package_id,
        "--source", "msstore",
        "--accept-source-agreements",
        "--accept-package-agreements",
    ]
    logs: list[str] = [f"Running: {' '.join(cmd)}"]
    logger.info("[InstallAgent] winget msstore cmd: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"winget store timed out after {TIMEOUT_SEC}s", logs, None
    except FileNotFoundError:
        return False, "winget.exe not found", logs, None

    logs.append(f"stdout: {proc.stdout[:2000]}")
    if proc.stderr:
        logs.append(f"stderr: {proc.stderr[:500]}")

    code = proc.returncode
    success = code in _WINGET_SUCCESS_CODES
    stdout_lower = proc.stdout.lower()
    if not success and any(phrase in stdout_lower for phrase in [
        "already installed", "no available upgrade", "no newer package"
    ]):
        success = True
        logs.append("Treating as success: software already present on system")

    version = _extract_version(proc.stdout) if success else None
    error   = None if success else f"winget store failed: exit {code}\n{proc.stderr[:300]}"
    return success, error or "", logs, version


def _run_brew(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """macOS Homebrew install."""
    brew = shutil.which("brew")
    if not brew:
        return False, "Homebrew not found. Install from https://brew.sh", [], None

    cmd = [brew, "install", "--cask", package_id]
    logs: list[str] = [f"Running: {' '.join(cmd)}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return False, f"brew timed out after {TIMEOUT_SEC}s", logs, None

    logs.append(proc.stdout[:2000])
    success = proc.returncode == 0
    error   = None if success else f"brew failed (exit {proc.returncode}): {proc.stderr[:300]}"
    version = _extract_version(proc.stdout) if success else None
    return success, error or "", logs, version


def _run_apt(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """Linux apt install."""
    cmd = ["sudo", "apt-get", "install", "-y", package_id]
    logs: list[str] = [f"Running: {' '.join(cmd)}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return False, f"apt timed out after {TIMEOUT_SEC}s", logs, None

    logs.append(proc.stdout[:2000])
    success = proc.returncode == 0
    error   = None if success else f"apt failed (exit {proc.returncode}): {proc.stderr[:300]}"
    version = _extract_version(proc.stdout) if success else None
    return success, error or "", logs, version


def _run_snap(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """Linux snap install."""
    cmd = ["sudo", "snap", "install", package_id, "--classic"]
    logs: list[str] = [f"Running: {' '.join(cmd)}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return False, f"snap timed out after {TIMEOUT_SEC}s", logs, None

    logs.append(proc.stdout[:2000])
    success = proc.returncode == 0
    error   = None if success else f"snap failed (exit {proc.returncode}): {proc.stderr[:300]}"
    version = _extract_version(proc.stdout) if success else None
    return success, error or "", logs, version


def _run_installer_file(
    installer_path: str, os_target: OperatingSystem
) -> tuple[bool, str, list[str], Optional[str]]:
    """Run a downloaded installer file silently."""
    path = Path(installer_path)
    if not path.exists():
        return False, f"Installer file not found: {installer_path}", [], None

    logs: list[str] = [f"Running installer: {path.name}"]
    ext = path.suffix.lower()

    if os_target == OperatingSystem.WINDOWS:
        if ext == ".exe":
            cmd = [str(path), "/S", "/silent", "/quiet", "/norestart"]
        elif ext == ".msi":
            cmd = ["msiexec", "/i", str(path), "/quiet", "/norestart", "ALLUSERS=1"]
        else:
            return False, f"Unsupported installer type: {ext}", logs, None
    elif os_target == OperatingSystem.MACOS:
        if ext == ".dmg":
            mount_result = subprocess.run(
                ["hdiutil", "attach", str(path), "-nobrowse", "-quiet"],
                capture_output=True, text=True, timeout=60,
            )
            if mount_result.returncode != 0:
                return False, f"Failed to mount DMG: {mount_result.stderr[:200]}", logs, None
            logs.append(mount_result.stdout[:500])
            m = re.search(r"(/Volumes/[^\n]+)", mount_result.stdout)
            if not m:
                return False, "Could not find DMG mount point", logs, None
            mount_point = m.group(1).strip()
            apps = _glob.glob(f"{mount_point}/*.app")
            if apps:
                app = apps[0]
                cp_result = subprocess.run(
                    ["cp", "-R", app, "/Applications/"],
                    capture_output=True, text=True, timeout=120,
                )
                subprocess.run(["hdiutil", "detach", mount_point, "-quiet"],
                               capture_output=True, timeout=30)
                if cp_result.returncode != 0:
                    return False, f"Failed to copy app: {cp_result.stderr[:200]}", logs, None
                return True, "", logs, None
            return False, "No .app found in DMG", logs, None
        elif ext == ".pkg":
            cmd = ["sudo", "installer", "-pkg", str(path), "-target", "/"]
        else:
            return False, f"Unsupported macOS installer type: {ext}", logs, None
    elif os_target == OperatingSystem.LINUX:
        if ext == ".deb":
            cmd = ["sudo", "dpkg", "-i", str(path)]
        elif ext == ".rpm":
            cmd = ["sudo", "rpm", "-ivh", str(path)]
        else:
            return False, f"Unsupported Linux installer type: {ext}", logs, None
    else:
        return False, f"Unknown OS: {os_target}", logs, None

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return False, f"Installer timed out after {TIMEOUT_SEC}s", logs, None

    logs.append(proc.stdout[:2000])
    success = proc.returncode == 0
    error   = None if success else f"Installer failed (exit {proc.returncode}): {proc.stderr[:300]}"
    version = _extract_version(proc.stdout) if success else None
    return success, error or "", logs, version


# ---------------------------------------------------------------------------
# Winget download-then-install (saves installer file to Desktop first)
# ---------------------------------------------------------------------------

def _run_winget_download_and_install(
    package_id: str, dest_dir: str
) -> tuple[bool, str, list[str], Optional[str], Optional[str]]:
    """
    1. winget download -> saves installer to dest_dir (Desktop)
    2. Runs the downloaded installer silently.
    Returns (success, error_msg, logs, version, local_path).
    """
    success, error, logs, local_path = _winget_download_only(package_id, dest_dir)
    if not success or not local_path:
        return False, error, logs, None, local_path

    ext = Path(local_path).suffix.lower()
    if ext == ".exe":
        run_cmd = [local_path, "/S", "/silent", "/quiet", "/norestart"]
    elif ext == ".msi":
        run_cmd = ["msiexec", "/i", local_path, "/quiet", "/norestart", "ALLUSERS=1"]
    else:
        return False, f"Unsupported installer type: {ext}", logs, None, local_path

    logs.append(f"Install cmd: {' '.join(run_cmd)}")
    try:
        inst_proc = subprocess.run(
            run_cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"Installer timed out after {TIMEOUT_SEC}s", logs, None, local_path

    logs.append(f"install stdout: {inst_proc.stdout[:2000]}")
    if inst_proc.stderr:
        logs.append(f"install stderr: {inst_proc.stderr[:500]}")

    success = inst_proc.returncode in (0, 3010)
    version = _extract_version(inst_proc.stdout) if success else None
    error   = None if success else (
        f"Installer failed (exit {inst_proc.returncode}): {inst_proc.stderr[:300]}"
    )
    return success, error or "", logs, version, local_path


def _winget_download_only(
    package_id: str, dest_dir: str
) -> tuple[bool, str, list[str], Optional[str]]:
    """
    Runs `winget download` ONLY — leaves the real installer file in dest_dir
    (the user's Desktop) without running/installing it. Used for the
    download_only intent so "download X" produces an actual file on disk.
    Returns (success, error_msg, logs, local_path).
    """
    winget = _find_winget()
    if not winget:
        return False, "winget not found.", [], None

    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    dl_cmd = [
        winget, "download",
        "--id", package_id,
        "--download-directory", dest_dir,
        "--accept-source-agreements",
        "--accept-package-agreements",
    ]
    logs: list[str] = [f"Download cmd: {' '.join(dl_cmd)}"]
    logger.info("[InstallAgent] winget download cmd: %s", " ".join(dl_cmd))

    try:
        dl_proc = subprocess.run(
            dl_cmd, capture_output=True, text=True, timeout=TIMEOUT_SEC,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"winget download timed out after {TIMEOUT_SEC}s", logs, None
    except FileNotFoundError:
        return False, "winget.exe not found", logs, None

    logs.append(f"download stdout: {dl_proc.stdout[:2000]}")
    if dl_proc.stderr:
        logs.append(f"download stderr: {dl_proc.stderr[:500]}")

    candidates = (
        _glob.glob(str(Path(dest_dir) / "*.exe")) +
        _glob.glob(str(Path(dest_dir) / "*.msi"))
    )
    if dl_proc.returncode not in (0, 1) and not candidates:
        code_msg = _WINGET_EXIT_CODES.get(dl_proc.returncode, f"exit {dl_proc.returncode}")
        return False, f"winget download failed: {code_msg}", logs, None
    if not candidates:
        return False, f"winget download ran but no installer found in {dest_dir}", logs, None

    installer_path = max(candidates, key=lambda p: Path(p).stat().st_mtime)
    logs.append(f"Installer saved to: {installer_path}")
    logger.info("[InstallAgent] Installer downloaded to: %s", installer_path)
    return True, "", logs, installer_path


# ---------------------------------------------------------------------------
# InstallAgent
# ---------------------------------------------------------------------------

class InstallAgent:
    """
    Agent 6 — Install Agent.

    Package-identity resolution (deciding WHAT package id to use) has
    already happened upstream in SoftwareResolverAgent + PlannerAgent. This
    agent only EXECUTES — it never looks up or guesses a package id itself.
    """

    async def install(
        self,
        download: DownloadResult,
        software: str,
        os_target: OperatingSystem,
        package_id: Optional[str] = None,
        use_package_manager: bool = False,
        rag_context: Optional[str] = None,
        install_method_override: Optional[str] = None,
    ) -> InstallResult:
        t0 = time.perf_counter()
        logger.info(
            "[InstallAgent] software=%r os=%s pkg_mgr=%s installer=%s",
            software, os_target.value, use_package_manager,
            download.local_path or "N/A",
        )

        if use_package_manager or download.local_path == "__use_package_manager__":
            if not package_id:
                return InstallResult(
                    success=False, install_method="none",
                    error=(
                        "No package id was resolved for this software. "
                        "The software resolver could not find it in the live "
                        "package manager catalogue."
                    ),
                )

            if install_method_override == "winget_store" and os_target == OperatingSystem.WINDOWS:
                success, error, logs, version = await asyncio.to_thread(_run_winget_store, package_id)
                duration = time.perf_counter() - t0
                return InstallResult(
                    success=success, install_method="winget_store",
                    version_installed=version, install_duration_sec=round(duration, 2),
                    logs=logs, error=error if not success else None,
                )

            if settings.features.download_to_desktop and os_target == OperatingSystem.WINDOWS:
                return await self._install_via_download_to_desktop(
                    software=software, package_id=package_id, t0=t0,
                )
            return await self._install_via_package_manager(
                software=software, os_target=os_target, package_id=package_id, t0=t0,
            )

        # ── Installer file path (browser-discovered download) ──────────────
        if not download.success or not download.local_path:
            return InstallResult(
                success=False, install_method="none",
                error="No installer file available",
            )

        success, error, logs, version = await asyncio.to_thread(
            _run_installer_file, download.local_path, os_target
        )
        duration = time.perf_counter() - t0
        return InstallResult(
            success=success, install_method="installer_file",
            version_installed=version, install_duration_sec=round(duration, 2),
            logs=logs, error=error if not success else None,
        )

    async def download_only(
        self,
        software: str,
        os_target: OperatingSystem,
        package_id: Optional[str],
        install_method: Optional[str],
    ) -> DownloadResult:
        """
        Executes the download_only intent for real: downloads the installer
        file to the user's Desktop via the package manager's native download
        command, WITHOUT running/installing it.

        This is the fix for "download X" never producing anything on disk —
        previously the workflow short-circuited this intent with a fake
        `__use_package_manager__` marker and never called into this agent.
        """
        if not package_id:
            return DownloadResult(
                success=False,
                error=(
                    "No package id was resolved for this software — cannot "
                    "download. The software resolver found no match in the "
                    "live package manager catalogue."
                ),
            )

        if os_target == OperatingSystem.WINDOWS:
            dest_dir = settings.desktop_dir
            success, error, logs, local_path = await asyncio.to_thread(
                _winget_download_only, package_id, dest_dir
            )
            if success and local_path:
                file_name = Path(local_path).name
                size = Path(local_path).stat().st_size
                logger.info(
                    "[InstallAgent] ✓ Downloaded %r to Desktop (no install): %s",
                    software, local_path,
                )
                return DownloadResult(
                    success=True, local_path=local_path, file_name=file_name,
                    file_size_bytes=size, verification="skipped",
                )
            return DownloadResult(success=False, error=error or "Download failed")

        if os_target == OperatingSystem.MACOS:
            # Homebrew has no "download only, don't install" primitive for
            # casks — `brew fetch --cask` downloads the artifact to the
            # brew cache without installing it, which is the closest
            # equivalent. We then copy it to the Desktop for the user.
            brew = shutil.which("brew")
            if not brew:
                return DownloadResult(success=False, error="Homebrew not found. Install from https://brew.sh")
            rc, out, err, cached_path = await asyncio.to_thread(_brew_fetch_cask, package_id)
            if rc == 0 and cached_path:
                dest = Path(settings.desktop_dir) / Path(cached_path).name
                try:
                    shutil.copy(cached_path, dest)
                except Exception as exc:
                    return DownloadResult(success=False, error=f"Downloaded but couldn't copy to Desktop: {exc}")
                return DownloadResult(
                    success=True, local_path=str(dest), file_name=dest.name,
                    file_size_bytes=dest.stat().st_size, verification="skipped",
                )
            return DownloadResult(success=False, error=err or "brew fetch failed")

        return DownloadResult(
            success=False,
            error=f"Download-only via package manager isn't supported on {os_target.value} yet.",
        )

    async def _install_via_download_to_desktop(
        self, software: str, package_id: str, t0: float,
    ) -> InstallResult:
        """Download installer to Desktop via winget, then run it silently."""
        dest_dir = settings.desktop_dir
        logger.info("[InstallAgent] Downloading %r to Desktop: %s", package_id, dest_dir)
        success, error, logs, version, local_path = await asyncio.to_thread(
            _run_winget_download_and_install, package_id, dest_dir
        )
        duration = time.perf_counter() - t0

        if success:
            logger.info(
                "[InstallAgent] ✓ %r installed successfully via winget (%.1fs). Installer on Desktop: %s",
                software, duration, local_path,
            )

        return InstallResult(
            success=success, install_method="winget_desktop_download",
            install_path=local_path, version_installed=version,
            install_duration_sec=round(duration, 2), logs=logs,
            error=error if not success else None,
        )

    async def _install_via_package_manager(
        self, software: str, os_target: OperatingSystem, package_id: str, t0: float,
    ) -> InstallResult:
        """Run the appropriate package manager for the target OS."""
        if os_target == OperatingSystem.WINDOWS:
            if _is_store_id(package_id):
                method = "winget_store"
                success, error, logs, version = await asyncio.to_thread(_run_winget_store, package_id)
            else:
                method = "winget"
                success, error, logs, version = await asyncio.to_thread(_run_winget, package_id)

        elif os_target == OperatingSystem.MACOS:
            method = "brew"
            success, error, logs, version = await asyncio.to_thread(_run_brew, package_id)

        elif os_target == OperatingSystem.LINUX:
            method = "apt"
            success, error, logs, version = await asyncio.to_thread(_run_apt, package_id)
            if not success and "not found" in (error or "").lower():
                method = "snap"
                success, error, logs, version = await asyncio.to_thread(_run_snap, package_id)
        else:
            return InstallResult(
                success=False, install_method="none",
                error=f"No package manager supported for OS: {os_target.value}",
            )

        duration = time.perf_counter() - t0
        if success:
            logger.info(
                "[InstallAgent] ✓ %r installed successfully via %s (%.1fs)",
                software, method, duration,
            )

        return InstallResult(
            success=success, install_method=method, version_installed=version,
            install_duration_sec=round(duration, 2), logs=logs,
            error=error if not success else None,
        )


def _brew_fetch_cask(package_id: str) -> tuple[int, str, str, Optional[str]]:
    """Downloads a cask artifact via `brew fetch --cask` without installing it."""
    brew = shutil.which("brew")
    try:
        proc = subprocess.run(
            [brew, "fetch", "--cask", package_id],
            capture_output=True, text=True, timeout=TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return -1, "", "brew fetch timed out", None

    cached_path = None
    m = re.search(r"Downloaded to:\s*(\S+)", proc.stdout + proc.stderr)
    if m:
        cached_path = m.group(1).strip()
    return proc.returncode, proc.stdout, proc.stderr, cached_path