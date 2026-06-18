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

    # 2. `where <exe>` — reliable on Windows PATH. Candidate exe names are
    #    derived dynamically from the software name (no hardcoded per-app map).
    exe_candidates = _exe_names(software, pkg_id)
    for exe in exe_candidates:
        rc, out, _ = _run(["where", exe])
        if rc == 0 and out.strip():
            ver_rc, ver_out, _ = _run([exe, "--version"])
            version = _extract_version(ver_out) if ver_rc == 0 else None
            return True, f"where:{exe}", version

    # 3. Dynamic filesystem scan: GUI apps that don't register on PATH still
    #    almost always land under Program Files / LocalAppData with a folder
    #    or exe name close to the product name. No hardcoded per-app dict —
    #    this glob-scans the real filesystem and fuzzy-matches the name.
    install_path = _find_install_path_dynamically(software)
    if install_path:
        return True, f"app_path:{install_path}", None

    return False, "not_found", None


def _find_install_path_dynamically(software: str) -> Optional[str]:
    """
    Dynamically searches common Windows install roots for an .exe whose
    path/name plausibly matches `software`, instead of relying on a
    hardcoded per-app path dictionary. This scales to any software without
    needing a new dict entry per app.
    """
    import glob as _glob

    local_app      = os.environ.get("LOCALAPPDATA", "")
    prog_files     = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    prog_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")

    # Build a loose match token: strip spaces/punctuation, lowercase.
    # e.g. "OBS Studio" -> "obsstudio", "VLC Media Player" -> "vlcmediaplayer"
    target = re.sub(r"[^a-z0-9]", "", software.lower())
    if not target:
        return None

    search_roots = [local_app, prog_files, prog_files_x86]
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            # Depth-limited glob: root/*/**.exe (covers the vast majority of
            # installer layouts: Root/AppName/AppName.exe or
            # Root/AppName/bin/AppName.exe etc.)
            pattern = os.path.join(root, "*", "**", "*.exe")
            for path in _glob.iglob(pattern, recursive=True):
                rel = os.path.relpath(path, root)
                rel_token = re.sub(r"[^a-z0-9]", "", rel.lower())
                if target in rel_token:
                    return path
        except (OSError, ValueError):
            continue
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

    exe_candidates = _exe_names(software, pkg_id)
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

    exe_candidates = _exe_names(software, pkg_id)
    for exe in exe_candidates:
        path = shutil.which(exe)
        if path:
            ver_rc, ver_out, _ = _run([exe, "--version"])
            version = _extract_version(ver_out) if ver_rc == 0 else None
            return True, f"which:{exe}", version

    return False, "not_found", None


def _exe_names(software: str, package_id: Optional[str] = None) -> list[str]:
    """
    Generates plausible CLI executable name candidates from the software
    name (and, if available, the resolved package id) — no static per-app
    dictionary, consistent with the rest of the system's design.

    This is a best-effort heuristic only. The canonical signal is `winget
    list` / `brew list` / `dpkg -l` (tried first in each _verify_* function);
    this is the second-tier check, and if every guess here misses too,
    `_find_install_path_dynamically` (a real filesystem glob+fuzzy-match
    scan) is the final fallback — so an imperfect guess here is never fatal.
    """
    sw = (software or "").strip()
    if not sw:
        return []

    words = re.findall(r"[A-Za-z0-9+#.]+", sw)
    if not words:
        return []

    candidates: list[str] = []

    def _add(token: str) -> None:
        token = token.strip().lower()
        if token and token not in candidates:
            candidates.append(token)

    _add("".join(words))                       # "visualstudiocode"
    _add("-".join(w.lower() for w in words))   # "visual-studio-code"
    _add(words[-1])                            # "code"  (often the real binary)
    _add(words[0])                             # "visual"

    # The package id frequently encodes the real binary name, e.g.
    # "Microsoft.VisualStudioCode" -> "visualstudiocode",
    # "VideoLAN.VLC" -> "vlc", "Mozilla.Firefox" -> "firefox"
    if package_id:
        tail = package_id.split(".")[-1]
        _add(re.sub(r"[^a-z0-9]", "", tail.lower()))

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