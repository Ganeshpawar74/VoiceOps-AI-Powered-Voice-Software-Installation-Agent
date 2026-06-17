"""
InstallAgent 

"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
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
# Winget package ID map  (extend as needed)
# ---------------------------------------------------------------------------

_WINGET_IDS: dict[str, str] = {
    "google chrome":          "Google.Chrome",
    "chrome":                 "Google.Chrome",
    "visual studio code":     "Microsoft.VisualStudioCode",
    "vs code":                "Microsoft.VisualStudioCode",
    "vscode":                 "Microsoft.VisualStudioCode",
    "python":                 "Python.Python.3.12",
    "python 3.12":            "Python.Python.3.12",
    "python 3.11":            "Python.Python.3.11",
    "node.js":                "OpenJS.NodeJS.LTS",
    "nodejs":                 "OpenJS.NodeJS.LTS",
    "node":                   "OpenJS.NodeJS.LTS",
    "git":                    "Git.Git",
    "vlc":                    "VideoLAN.VLC",
    "vlc media player":       "VideoLAN.VLC",
    "discord":                "Discord.Discord",
    "zoom":                   "Zoom.Zoom",
    "7-zip":                  "7zip.7zip",
    "7zip":                   "7zip.7zip",
    "winrar":                 "RARLab.WinRAR",
    "notepad++":              "Notepad++.Notepad++",
    "slack":                  "SlackTechnologies.Slack",
    "firefox":                "Mozilla.Firefox",
    "postman":                "Postman.Postman",
    "docker":                 "Docker.DockerDesktop",
    "docker desktop":         "Docker.DockerDesktop",
}

# Merge with settings registry
def _get_winget_id(software: str) -> Optional[str]:
    key = software.lower().strip()
    # 1. In-agent map
    if key in _WINGET_IDS:
        return _WINGET_IDS[key]
    # 2. Settings registry (winget_packages keyed by canonical name)
    reg = settings.registry.winget_packages
    for reg_key, pkg_id in reg.items():
        if reg_key.lower() == key:
            return pkg_id
    return None


# Winget exit code → human readable
# NOTE: winget returns unsigned 32-bit HRESULT codes on Windows.
# Python receives them as unsigned ints (e.g. 2316632107 not -1978335189).
# We store both signed and unsigned forms so lookup works regardless.
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
    # HRESULT codes — stored as both signed and unsigned
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

# Exit codes that mean "software is present / already installed" — treat as success
_WINGET_SUCCESS_CODES = {
    0,
    3010,                      # reboot required but installed
    1638,                      # another version present
    _u32(-1978335189),         # 2316632107 — already installed, no upgrade
    -1978335189,
    _u32(-1978335215),         # 2316632081 — no upgrade found (already latest)
    -1978335215,
}

TIMEOUT_SEC = 300   # 5 min max per install


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_winget() -> Optional[str]:
    """Locate winget.exe — checks PATH and AppInstaller location."""
    import shutil
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
    """
    Synchronous winget call — run inside asyncio.to_thread().
    Returns (success, error_msg, logs, version_installed).
    """
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
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
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

    # Check against full success set (handles both signed and unsigned HRESULT)
    success = code in _WINGET_SUCCESS_CODES

    # Also catch "already installed" in stdout as a safety net
    stdout_lower = proc.stdout.lower()
    if not success and any(phrase in stdout_lower for phrase in [
        "already installed", "no available upgrade", "no newer package"
    ]):
        success = True
        logs.append("Treating as success: software already present on system")

    version = _extract_version(proc.stdout) if success else None
    error   = None if success else f"winget failed: {code_msg}\n{proc.stderr[:300]}"
    return success, error or "", logs, version


def _run_brew(package_id: str) -> tuple[bool, str, list[str], Optional[str]]:
    """macOS Homebrew install."""
    import shutil
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


def _run_installer_file(installer_path: str, os_target: OperatingSystem) -> tuple[bool, str, list[str], Optional[str]]:
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
            # Mount DMG, find .app, copy to /Applications
            mount_result = subprocess.run(
                ["hdiutil", "attach", str(path), "-nobrowse", "-quiet"],
                capture_output=True, text=True, timeout=60,
            )
            if mount_result.returncode != 0:
                return False, f"Failed to mount DMG: {mount_result.stderr[:200]}", logs, None
            logs.append(mount_result.stdout[:500])
            # Find mount point
            m = re.search(r"(/Volumes/[^\n]+)", mount_result.stdout)
            if not m:
                return False, "Could not find DMG mount point", logs, None
            mount_point = m.group(1).strip()
            # Copy .app to Applications
            import glob
            apps = glob.glob(f"{mount_point}/*.app")
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
    1. winget download → saves installer to dest_dir (Desktop)
    2. Runs the downloaded installer silently.
    Returns (success, error_msg, logs, version, local_path).
    """
    import glob as _glob
    winget = _find_winget()
    if not winget:
        return False, "winget not found.", [], None, None

    Path(dest_dir).mkdir(parents=True, exist_ok=True)

    # ── Step 1: winget download ──────────────────────────────────────────
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
            dl_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"winget download timed out after {TIMEOUT_SEC}s", logs, None, None
    except FileNotFoundError:
        return False, "winget.exe not found", logs, None, None

    logs.append(f"download stdout: {dl_proc.stdout[:2000]}")
    if dl_proc.stderr:
        logs.append(f"download stderr: {dl_proc.stderr[:500]}")

    if dl_proc.returncode not in (0, 1):   # winget download may exit 1 on warnings
        # Check if a file was actually saved despite non-zero exit
        candidates = (
            _glob.glob(str(Path(dest_dir) / "*.exe")) +
            _glob.glob(str(Path(dest_dir) / "*.msi"))
        )
        if not candidates:
            code_msg = _WINGET_EXIT_CODES.get(dl_proc.returncode, f"exit {dl_proc.returncode}")
            return False, f"winget download failed: {code_msg}", logs, None, None

    # Find the downloaded installer (most recently modified .exe / .msi in dest_dir)
    candidates = (
        _glob.glob(str(Path(dest_dir) / "*.exe")) +
        _glob.glob(str(Path(dest_dir) / "*.msi"))
    )
    if not candidates:
        return False, f"winget download ran but no installer found in {dest_dir}", logs, None, None

    installer_path = max(candidates, key=lambda p: Path(p).stat().st_mtime)
    logs.append(f"Installer saved to: {installer_path}")
    logger.info("[InstallAgent] Installer downloaded to: %s", installer_path)

    # ── Step 2: run the installer silently ──────────────────────────────
    ext = Path(installer_path).suffix.lower()
    if ext == ".exe":
        run_cmd = [installer_path, "/S", "/silent", "/quiet", "/norestart"]
    elif ext == ".msi":
        run_cmd = ["msiexec", "/i", installer_path, "/quiet", "/norestart", "ALLUSERS=1"]
    else:
        return False, f"Unsupported installer type: {ext}", logs, None, installer_path

    logs.append(f"Install cmd: {' '.join(run_cmd)}")
    try:
        inst_proc = subprocess.run(
            run_cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"Installer timed out after {TIMEOUT_SEC}s", logs, None, installer_path

    logs.append(f"install stdout: {inst_proc.stdout[:2000]}")
    if inst_proc.stderr:
        logs.append(f"install stderr: {inst_proc.stderr[:500]}")

    success = inst_proc.returncode in (0, 3010)  # 3010 = reboot required but installed
    version = _extract_version(inst_proc.stdout) if success else None
    error   = None if success else (
        f"Installer failed (exit {inst_proc.returncode}): {inst_proc.stderr[:300]}"
    )
    return success, error or "", logs, version, installer_path


# ---------------------------------------------------------------------------
# InstallAgent
# ---------------------------------------------------------------------------

class InstallAgent:
    """Agent 6 — Install Agent."""

    async def install(
        self,
        download: DownloadResult,
        software: str,
        os_target: OperatingSystem,
        package_id: Optional[str] = None,
        use_package_manager: bool = False,
        rag_context: Optional[str] = None,
    ) -> InstallResult:
        t0 = time.perf_counter()
        logger.info(
            "[InstallAgent] software=%r os=%s pkg_mgr=%s installer=%s",
            software, os_target.value, use_package_manager,
            download.local_path or "N/A",
        )

        # ── Package-manager path ──────────────────────────────────────────────
        if use_package_manager or (
            download.local_path == "__use_package_manager__"
        ):
            # If download_to_desktop is enabled on Windows, download the installer
            # file to the Desktop first, then run it silently.
            if (
                settings.features.download_to_desktop
                and os_target == OperatingSystem.WINDOWS
            ):
                return await self._install_via_download_to_desktop(
                    software=software,
                    package_id=package_id,
                    t0=t0,
                )
            return await self._install_via_package_manager(
                software=software,
                os_target=os_target,
                package_id=package_id,
                t0=t0,
            )

        # ── Installer file path ───────────────────────────────────────────────
        if not download.success or not download.local_path:
            return InstallResult(
                success=False,
                install_method="none",
                error="No installer file available",
            )

        success, error, logs, version = await asyncio.to_thread(
            _run_installer_file, download.local_path, os_target
        )

        duration = time.perf_counter() - t0
        return InstallResult(
            success=success,
            install_method="installer_file",
            version_installed=version,
            install_duration_sec=round(duration, 2),
            logs=logs,
            error=error if not success else None,
        )

    async def _install_via_download_to_desktop(
        self,
        software: str,
        package_id: Optional[str],
        t0: float,
    ) -> InstallResult:
        """Download installer to Desktop via winget, then run it silently."""
        pkg_id = package_id or _get_winget_id(software) or software
        dest_dir = settings.desktop_dir

        logger.info(
            "[InstallAgent] Downloading %r to Desktop: %s", pkg_id, dest_dir
        )
        success, error, logs, version, local_path = await asyncio.to_thread(
            _run_winget_download_and_install, pkg_id, dest_dir
        )
        duration = time.perf_counter() - t0
        return InstallResult(
            success=success,
            install_method="winget_desktop_download",
            install_path=local_path,
            version_installed=version,
            install_duration_sec=round(duration, 2),
            logs=logs,
            error=error if not success else None,
        )

    async def _install_via_package_manager(
        self,
        software: str,
        os_target: OperatingSystem,
        package_id: Optional[str],
        t0: float,
    ) -> InstallResult:
        """Run the appropriate package manager for the target OS."""

        # Resolve package ID
        pkg_id = package_id or _get_winget_id(software)

        if os_target == OperatingSystem.WINDOWS:
            if not pkg_id:
                # Last-resort: try software name directly
                pkg_id = software
            method = "winget"
            success, error, logs, version = await asyncio.to_thread(
                _run_winget, pkg_id
            )

        elif os_target == OperatingSystem.MACOS:
            if not pkg_id:
                reg = settings.registry.brew_packages
                pkg_id = reg.get(software) or software.lower().replace(" ", "-")
            method = "brew"
            success, error, logs, version = await asyncio.to_thread(
                _run_brew, pkg_id
            )

        elif os_target == OperatingSystem.LINUX:
            # Try apt first, fall back to snap
            apt_id  = settings.registry.apt_packages.get(software)
            snap_id = settings.registry.snap_packages.get(software)

            if apt_id:
                method = "apt"
                success, error, logs, version = await asyncio.to_thread(
                    _run_apt, apt_id
                )
            elif snap_id:
                method = "snap"
                success, error, logs, version = await asyncio.to_thread(
                    _run_snap, snap_id
                )
            else:
                # Try apt with software name directly
                method = "apt"
                success, error, logs, version = await asyncio.to_thread(
                    _run_apt, software.lower()
                )
        else:
            return InstallResult(
                success=False,
                install_method="none",
                error=f"No package manager supported for OS: {os_target.value}",
            )

        duration = time.perf_counter() - t0
        return InstallResult(
            success=success,
            install_method=method,
            version_installed=version,
            install_duration_sec=round(duration, 2),
            logs=logs,
            error=error if not success else None,
        )