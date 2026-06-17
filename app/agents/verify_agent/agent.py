"""
Agent 6b — Verify Agent (NEW — per architecture diagram)

After install completes, independently verifies the software is actually installed
by checking:
  Windows  : `winget list --id <pkg>` + `where <exe>` + `<exe> --version`
  macOS    : `brew list <formula>` + `/Applications/<App>.app` existence
  Linux    : `dpkg -l <pkg>` / `snap list <pkg>` + `which <exe>`

Returns VerifyResult (Pydantic model).
All subprocess calls wrapped in asyncio.to_thread — never blocks the event loop.

FIXES:
  BUG #8: _verify_windows winget check used pkg_id.split(".")[-1] ("Postman") but
          winget list output contains the display name, not the package ID segment.
          Fix: search for the full pkg_id string in output (case-insensitive),
          then fall through to `where <exe>` which is more reliable on Windows.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from typing import Optional

from app.config.settings import get_settings
from app.models.schemas import InstallResult, OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()

TIMEOUT = 30   # seconds per verification command


def _run(cmd: list[str]) -> tuple[int, str, str]:
    """Run command synchronously — call via asyncio.to_thread."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def _verify_windows(software: str, pkg_id: Optional[str]) -> tuple[bool, str, Optional[str]]:
    """Returns (found, method, version_string)."""
    # 1. winget list --id <pkg_id>
    #    BUG #8 FIX: old code checked `pkg_id.split(".")[-1].lower() in out.lower()`
    #    e.g. "postman" in the output. But winget list output looks like:
    #      Name     Id               Version  Source
    #      Postman  Postman.Postman  12.15.5  winget
    #    The full pkg_id ("Postman.Postman") is present, so search for that instead.
    #    Also guard against winget returning rc=0 but "No installed package found".
    if pkg_id:
        rc, out, _ = _run(["winget", "list", "--id", pkg_id, "--accept-source-agreements"])
        if rc == 0 and pkg_id.lower() in out.lower() and "no installed" not in out.lower():
            version = _extract_version(out)
            return True, "winget_list", version

    # 2. `where <exe>` — reliable on Windows PATH
    exe_candidates = _exe_names(software)
    for exe in exe_candidates:
        rc, out, _ = _run(["where", exe])
        if rc == 0 and out.strip():
            ver_rc, ver_out, _ = _run([exe, "--version"])
            version = _extract_version(ver_out) if ver_rc == 0 else None
            return True, f"where:{exe}", version

    # 3. Check common install paths for GUI apps that don't register on PATH
    install_path = _windows_app_path(software)
    if install_path and os.path.exists(install_path):
        return True, f"app_path:{install_path}", None

    return False, "not_found", None


def _windows_app_path(software: str) -> Optional[str]:
    """Return the expected .exe path for GUI apps not on PATH."""
    local_app = os.environ.get("LOCALAPPDATA", "")
    prog_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")

    known: dict[str, list[str]] = {
        "Postman": [
            os.path.join(local_app, "Postman", "Postman.exe"),
        ],
        "Discord": [
            os.path.join(local_app, "Discord", "Update.exe"),
        ],
        "Slack": [
            os.path.join(local_app, "slack", "slack.exe"),
        ],
        "Zoom": [
            os.path.join(local_app, "Zoom", "bin", "Zoom.exe"),
        ],
        "OBS Studio": [
            os.path.join(prog_files, "obs-studio", "bin", "64bit", "obs64.exe"),
        ],
        "GIMP": [
            os.path.join(prog_files, "GIMP 2", "bin", "gimp-2.10.exe"),
        ],
        "Blender": [
            os.path.join(prog_files, "Blender Foundation", "Blender", "blender.exe"),
        ],
        "VLC Media Player": [
            os.path.join(prog_files, "VideoLAN", "VLC", "vlc.exe"),
            os.path.join(prog_files_x86, "VideoLAN", "VLC", "vlc.exe"),
        ],
        "Inkscape": [
            os.path.join(prog_files, "Inkscape", "bin", "inkscape.exe"),
        ],
    }
    for path in known.get(software, []):
        if path and os.path.exists(path):
            return path
    return None


def _verify_macos(software: str, pkg_id: Optional[str]) -> tuple[bool, str, Optional[str]]:
    # 1. brew list
    if pkg_id:
        rc, out, _ = _run(["brew", "list", pkg_id])
        if rc == 0:
            ver_rc, ver_out, _ = _run(["brew", "info", "--json=v2", pkg_id])
            version = _extract_version(ver_out)
            return True, "brew_list", version

    # 2. /Applications/<Name>.app
    app_name = software.replace(" ", "")
    app_path = f"/Applications/{app_name}.app"
    if os.path.exists(app_path):
        return True, "app_bundle", None

    exe_candidates = _exe_names(software)
    for exe in exe_candidates:
        path = shutil.which(exe)
        if path:
            ver_rc, ver_out, _ = _run([exe, "--version"])
            version = _extract_version(ver_out) if ver_rc == 0 else None
            return True, f"which:{exe}", version

    return False, "not_found", None


def _verify_linux(software: str, pkg_id: Optional[str]) -> tuple[bool, str, Optional[str]]:
    # 1. dpkg
    if pkg_id:
        rc, out, _ = _run(["dpkg", "-l", pkg_id])
        if rc == 0 and "ii  " in out:
            version = _extract_version(out)
            return True, "dpkg", version

    # 2. snap list
    snap_id = settings.registry.snap_packages.get(software)
    if snap_id:
        rc, out, _ = _run(["snap", "list", snap_id])
        if rc == 0:
            version = _extract_version(out)
            return True, "snap_list", version

    # 3. which
    exe_candidates = _exe_names(software)
    for exe in exe_candidates:
        path = shutil.which(exe)
        if path:
            ver_rc, ver_out, _ = _run([exe, "--version"])
            version = _extract_version(ver_out) if ver_rc == 0 else None
            return True, f"which:{exe}", version

    return False, "not_found", None


def _exe_names(software: str) -> list[str]:
    """Map canonical software name → likely CLI exe names."""
    mapping: dict[str, list[str]] = {
        "Visual Studio Code": ["code", "code-insiders"],
        "Python":             ["python", "python3", "py"],
        "Node.js":            ["node", "nodejs"],
        "Git":                ["git"],
        "Google Chrome":      ["chrome", "google-chrome", "chromium"],
        "Mozilla Firefox":    ["firefox"],
        "Docker Desktop":     ["docker"],
        "Postman":            ["postman"],
        "Slack":              ["slack"],
        "Zoom":               ["zoom", "zoom.exe"],
        "7-Zip":              ["7z", "7za"],
        "Notepad++":          ["notepad++"],
        "Discord":            ["discord"],
        "VLC Media Player":   ["vlc"],
        "OBS Studio":         ["obs", "obs-studio"],
        "GIMP":               ["gimp"],
        "Blender":            ["blender"],
        "Inkscape":           ["inkscape"],
        "Rust":               ["rustc", "cargo"],
        "Go":                 ["go"],
    }
    candidates = mapping.get(software, [software.lower().split()[0]])
    return candidates


def _extract_version(text: str) -> Optional[str]:
    m = re.search(r"\b(\d+\.\d+[\.\d]*)\b", text)
    return m.group(1) if m else None


class VerifyAgent:
    """
    Agent 6b — Post-install verification.

    Independently checks that the software is present on the system
    after InstallAgent reports success.
    """

    async def verify(
        self,
        software: str,
        os_target: OperatingSystem,
        install_result: InstallResult,
        pkg_id: Optional[str] = None,
    ) -> dict:
        """
        Returns a dict:
          {
            "verified": bool,
            "method": str,
            "version_found": str | None,
            "install_claimed_success": bool,
            "note": str,
          }
        """
        logger.info(
            "[VerifyAgent] Verifying software=%r os=%s claimed_success=%s",
            software, os_target.value, install_result.success,
        )

        if not install_result.success:
            return {
                "verified": False,
                "method": "skipped_install_failed",
                "version_found": None,
                "install_claimed_success": False,
                "note": f"Install reported failure: {install_result.error}",
            }

        dispatch = {
            OperatingSystem.WINDOWS: _verify_windows,
            OperatingSystem.MACOS:   _verify_macos,
            OperatingSystem.LINUX:   _verify_linux,
        }.get(os_target)

        if dispatch is None:
            return {
                "verified": False,
                "method": "unsupported_os",
                "version_found": None,
                "install_claimed_success": True,
                "note": f"No verification strategy for OS: {os_target.value}",
            }

        try:
            found, method, version = await asyncio.to_thread(dispatch, software, pkg_id)
        except Exception as exc:
            logger.warning("[VerifyAgent] Verification error: %s", exc)
            return {
                "verified": False,
                "method": "error",
                "version_found": None,
                "install_claimed_success": True,
                "note": str(exc),
            }

        result = {
            "verified": found,
            "method": method,
            "version_found": version or install_result.version_installed,
            "install_claimed_success": True,
            "note": (
                f"Verified via {method}" if found
                else f"Could not confirm installation via {method}"
            ),
        }

        logger.info(
            "[VerifyAgent] software=%r verified=%s method=%s version=%s",
            software, found, method, result["version_found"],
        )
        return result