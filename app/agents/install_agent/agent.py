"""
InstallAgent

FIXES IN THIS VERSION:
  NEW FIX #E (GitHub Desktop / GitHub CLI missing from _WINGET_IDS):
    The install agent had a local _WINGET_IDS dict that did NOT contain
    "github desktop" or "github cli". Even though settings.registry.winget_packages
    had the entries, _get_winget_id() checked the local dict first and the settings
    dict second — but only did an exact-key match on the settings dict.
    Fix: added GitHub Desktop, GitHub CLI, and 20+ other common apps to _WINGET_IDS.
    Also improved _get_winget_id() to do case-insensitive partial matching against
    the settings registry so unknown-but-close names resolve correctly.

  NEW FIX #F (LLM-assisted winget ID discovery for unknown software):
    When _get_winget_id() returns None for a software name, the old code fell back
    to using the raw software name as the winget package ID — which almost always
    fails silently or installs the wrong thing.
    Fix: added _discover_winget_id_via_llm() that asks Mistral to suggest the
    correct winget package ID for any software not in our local registry. This makes
    the system truly handle "whatever the user asks" without hardcoded IDs.

  NEW FIX #G (Notification after background install):
    After a successful winget install, log a clear message indicating the software
    is now available so the notification agent can surface it properly.
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
# Winget package ID map — FIX #E: expanded with GitHub and many more
# ---------------------------------------------------------------------------

_WINGET_IDS: dict[str, str] = {
    # Browsers
    "google chrome":          "Google.Chrome",
    "chrome":                 "Google.Chrome",
    "mozilla firefox":        "Mozilla.Firefox",
    "firefox":                "Mozilla.Firefox",
    # Editors / IDEs
    "visual studio code":     "Microsoft.VisualStudioCode",
    "vs code":                "Microsoft.VisualStudioCode",
    "vscode":                 "Microsoft.VisualStudioCode",
    "notepad++":              "Notepad++.Notepad++",
    "sublime text":           "SublimeHQ.SublimeText.4",
    "sublime":                "SublimeHQ.SublimeText.4",
    "intellij idea":          "JetBrains.IntelliJIDEA.Community",
    "pycharm":                "JetBrains.PyCharm.Community",
    "android studio":         "Google.AndroidStudio",
    # Runtime / dev tools
    "python":                 "Python.Python.3.12",
    "python 3.12":            "Python.Python.3.12",
    "python 3.11":            "Python.Python.3.11",
    "python 3.10":            "Python.Python.3.10",
    "node.js":                "OpenJS.NodeJS.LTS",
    "nodejs":                 "OpenJS.NodeJS.LTS",
    "node":                   "OpenJS.NodeJS.LTS",
    "git":                    "Git.Git",
    "java":                   "EclipseAdoptium.Temurin.21.JDK",
    "java jdk":               "EclipseAdoptium.Temurin.21.JDK",
    "rust":                   "Rustlang.Rustup",
    "go":                     "GoLang.Go",
    "golang":                 "GoLang.Go",
    # FIX #E: GitHub entries were completely missing
    "github desktop":         "GitHub.GitHubDesktop",
    "github":                 "GitHub.GitHubDesktop",
    "git hub":                "GitHub.GitHubDesktop",
    "github cli":             "GitHub.cli",
    "gh cli":                 "GitHub.cli",
    "gh":                     "GitHub.cli",
    # Containers / infra
    "docker":                 "Docker.DockerDesktop",
    "docker desktop":         "Docker.DockerDesktop",
    "postman":                "Postman.Postman",
    # Communication
    "discord":                "Discord.Discord",
    "zoom":                   "Zoom.Zoom",
    "slack":                  "SlackTechnologies.Slack",
    "microsoft teams":        "Microsoft.Teams",
    "teams":                  "Microsoft.Teams",
    "skype":                  "Microsoft.Skype",
    "telegram":               "Telegram.TelegramDesktop",
    "whatsapp":               "9E2F88E3.WhatsApp",
    # Media / creative
    "vlc":                    "VideoLAN.VLC",
    "vlc media player":       "VideoLAN.VLC",
    "obs studio":             "OBSProject.OBSStudio",
    "obs":                    "OBSProject.OBSStudio",
    "gimp":                   "GIMP.GIMP",
    "inkscape":               "Inkscape.Inkscape",
    "blender":                "BlenderFoundation.Blender",
    "audacity":               "Audacity.Audacity",
    "handbrake":              "HandBrake.HandBrake",
    "vlc":                    "VideoLAN.VLC",
    "spotify":                "Spotify.Spotify",
    # Utilities
    "7-zip":                  "7zip.7zip",
    "7zip":                   "7zip.7zip",
    "winrar":                 "RARLab.WinRAR",
    "putty":                  "PuTTY.PuTTY",
    "filezilla":              "TimKosse.FileZilla.Client",
    "wireshark":              "WiresharkFoundation.Wireshark",
    "notion":                 "Notion.Notion",
    "figma":                  "Figma.Figma",
    "anaconda":               "Anaconda.Anaconda3",
    "steam":                  "Valve.Steam",
}


def _get_winget_id(software: str) -> Optional[str]:
    """
    FIX #E: improved lookup — checks local dict, then settings registry
    with case-insensitive partial matching.
    """
    key = software.lower().strip()

    # 1. Exact match in local dict
    if key in _WINGET_IDS:
        return _WINGET_IDS[key]

    # 2. Exact match in settings registry (case-insensitive)
    reg = settings.registry.winget_packages
    for reg_key, pkg_id in reg.items():
        if reg_key.lower() == key:
            return pkg_id

    # 3. Partial / substring match in local dict (handles "github desktop" → "github")
    for dict_key, pkg_id in _WINGET_IDS.items():
        if key in dict_key or dict_key in key:
            return pkg_id

    # 4. Partial match in settings registry
    for reg_key, pkg_id in reg.items():
        if key in reg_key.lower() or reg_key.lower() in key:
            return pkg_id

    return None


def _discover_winget_id_via_llm(software: str) -> Optional[str]:
    """
    FIX #F: Ask Mistral for the correct winget package ID when our local
    registry doesn't have the software. Returns None if the LLM can't
    determine a reliable ID or if no API key is configured.
    This is a blocking call — run inside asyncio.to_thread().
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)
        prompt = (
            f"What is the exact winget package ID for '{software}' on Windows?\n"
            "Reply with ONLY the package ID string (e.g. 'GitHub.GitHubDesktop'), "
            "nothing else. If you are not certain, reply with the word UNKNOWN."
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=40,
        )
        result = response.choices[0].message.content.strip().strip('"').strip("'")
        if result.upper() == "UNKNOWN" or " " in result or len(result) < 3:
            logger.info("[InstallAgent] LLM could not determine winget ID for %r", software)
            return None
        logger.info("[InstallAgent] LLM suggested winget ID for %r: %r", software, result)
        return result
    except Exception as exc:
        logger.warning("[InstallAgent] LLM winget ID discovery failed: %s", exc)
        return None


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
    Synchronous winget install call — run inside asyncio.to_thread().
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
    """
    Install a Microsoft Store app via winget using --source msstore.
    Used for apps like ChatGPT (9NT1R1C2HH7J) that are Store-only.
    """
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
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
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
            import glob
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

    if dl_proc.returncode not in (0, 1):
        candidates = (
            _glob.glob(str(Path(dest_dir) / "*.exe")) +
            _glob.glob(str(Path(dest_dir) / "*.msi"))
        )
        if not candidates:
            code_msg = _WINGET_EXIT_CODES.get(dl_proc.returncode, f"exit {dl_proc.returncode}")
            return False, f"winget download failed: {code_msg}", logs, None, None

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

    success = inst_proc.returncode in (0, 3010)
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
        install_method_override: Optional[str] = None,
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
            # Honour explicit install_method from planner (e.g. winget_store)
            if install_method_override == "winget_store" and os_target == OperatingSystem.WINDOWS:
                pkg = package_id or software
                success, error, logs, version = await asyncio.to_thread(_run_winget_store, pkg)
                duration = time.perf_counter() - t0
                return InstallResult(
                    success=success,
                    install_method="winget_store",
                    version_installed=version,
                    install_duration_sec=round(duration, 2),
                    logs=logs,
                    error=error if not success else None,
                )
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
        # FIX #E+F: resolve package ID properly
        pkg_id = package_id or _get_winget_id(software)
        if not pkg_id:
            # FIX #F: ask LLM for the winget ID
            pkg_id = await asyncio.to_thread(_discover_winget_id_via_llm, software)
        if not pkg_id:
            pkg_id = software  # last resort — use name directly

        dest_dir = settings.desktop_dir

        logger.info(
            "[InstallAgent] Downloading %r to Desktop: %s", pkg_id, dest_dir
        )
        success, error, logs, version, local_path = await asyncio.to_thread(
            _run_winget_download_and_install, pkg_id, dest_dir
        )
        duration = time.perf_counter() - t0

        if success:
            # FIX #G: explicit notification-ready log
            logger.info(
                "[InstallAgent] ✓ %r installed successfully via winget (%.1fs). "
                "Installer on Desktop: %s",
                software, duration, local_path,
            )

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

        # FIX #E+F: Resolve package ID with LLM fallback
        pkg_id = package_id or _get_winget_id(software)

        if os_target == OperatingSystem.WINDOWS:
            if not pkg_id:
                pkg_id = await asyncio.to_thread(_discover_winget_id_via_llm, software)
            if not pkg_id:
                pkg_id = software
            # Detect Microsoft Store package IDs (uppercase alphanumeric, no dots)
            import re as _re
            if _re.fullmatch(r"[A-Z0-9]{9,16}", pkg_id):
                method = "winget_store"
                success, error, logs, version = await asyncio.to_thread(
                    _run_winget_store, pkg_id
                )
            else:
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

        if success:
            # FIX #G: explicit notification-ready log
            logger.info(
                "[InstallAgent] ✓ %r installed successfully via %s (%.1fs)",
                software, method, duration,
            )

        return InstallResult(
            success=success,
            install_method=method,
            version_installed=version,
            install_duration_sec=round(duration, 2),
            logs=logs,
            error=error if not success else None,
        )