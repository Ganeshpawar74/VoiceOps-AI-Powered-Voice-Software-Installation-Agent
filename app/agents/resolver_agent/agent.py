"""
Agent — SoftwareResolverAgent  (NEW)

Replaces every hardcoded software dict (_WINGET_IDS, winget_packages,
software_aliases, brew_packages, apt_packages, snap_packages, ...) with a
single Gen-AI-based resolution pipeline:

    user free-text software name
        |
        v
    1. winget search "<name>"      <-- ground truth: what's ACTUALLY installable
        |
        v
    2. If exactly one strong match -> use it directly, no LLM needed.
       If zero or many ambiguous matches -> ask the LLM to pick the best
       candidate id from the *actual* search results (never invent one).
        |
        v
    3. Cache the resolved (query -> package_id) pair in Redis for 30 days
       so repeated requests for the same software are instant and don't
       re-spend an LLM call. The cache is just a performance layer — the
       system never trusts a cached id without it having come from a real
       winget search at some point.

This means: no app-specific code, no per-app dict entries, no "if software
== 'chatgpt'" branches anywhere. Any software winget knows about resolves
correctly the first time a user asks for it, automatically.

For macOS / Linux the same shape is used against `brew search` / `apt-cache
search` / `snap find` respectively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config.settings import get_settings
from app.models.schemas import OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()

TIMEOUT_SEC = 30
CACHE_TTL_SEC = 60 * 60 * 24 * 30  # 30 days — purely a perf cache, not a source of truth


@dataclass
class ResolvedPackage:
    found: bool
    package_id: Optional[str] = None
    display_name: Optional[str] = None
    source: str = "none"          # winget | brew | apt | snap | llm_disambiguated | cache | none
    install_method: Optional[str] = None  # winget | winget_store | brew | apt | snap
    candidates_considered: int = 0
    confidence: float = 0.0
    note: str = ""


# ---------------------------------------------------------------------------
# Low-level package-manager search commands (ground truth, no hardcoding)
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = TIMEOUT_SEC) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", "executable not found"
    except subprocess.TimeoutExpired:
        return -1, "", "search timed out"


def _is_store_id(pkg_id: str) -> bool:
    """Microsoft Store IDs are uppercase alphanumeric, 9-16 chars, no dots."""
    return bool(re.fullmatch(r"[A-Z0-9]{9,16}", pkg_id))


def _winget_search(query: str) -> list[dict]:
    """
    Runs `winget search <query>` and parses the tabular output into structured
    candidates: [{name, id, version, source}, ...]. This is the live ground
    truth — nothing here is hardcoded per-software.
    """
    winget = shutil.which("winget")
    if not winget:
        local = _find_winget_fallback()
        winget = local or "winget"

    rc, out, err = _run([
        winget, "search", query,
        "--accept-source-agreements",
    ])
    if rc not in (0, 1):  # winget returns 1 for "no results" but still prints a table
        logger.warning("[ResolverAgent] winget search failed (rc=%s): %s", rc, err[:200])
        return []

    return _parse_winget_table(out)


def _find_winget_fallback() -> Optional[str]:
    from pathlib import Path
    p = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "winget.exe"
    return str(p) if p.exists() else None


def _parse_winget_table(stdout: str) -> list[dict]:
    """
    winget search output is a fixed-width table:
        Name              Id                       Version      Source
        ----------------------------------------------------------------
        ChatGPT            9NT1R1C2HH7J             Unknown      msstore
        Visual Studio Code Microsoft.VisualStudioCode 1.90.0     winget

    We locate the header row to learn each column's start offset, then slice
    every following row using those offsets — this is robust to names with
    spaces, which a naive .split() is not.
    """
    lines = [l for l in stdout.splitlines() if l.strip()]
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\bName\b", line) and re.search(r"\bId\b", line):
            header_idx = i
            break
    if header_idx is None:
        return []

    header = lines[header_idx]
    col_starts = {}
    for col in ("Name", "Id", "Version", "Source", "Match"):
        m = re.search(rf"\b{col}\b", header)
        if m:
            col_starts[col] = m.start()

    if "Name" not in col_starts or "Id" not in col_starts:
        return []

    ordered_cols = sorted(col_starts.items(), key=lambda kv: kv[1])

    candidates = []
    # Skip header + separator ("---...") rows
    for line in lines[header_idx + 1:]:
        if set(line.strip()) <= {"-"}:
            continue
        row = {}
        for idx, (col_name, start) in enumerate(ordered_cols):
            end = ordered_cols[idx + 1][1] if idx + 1 < len(ordered_cols) else len(line)
            row[col_name] = line[start:end].strip()
        if row.get("Name") and row.get("Id"):
            candidates.append({
                "name":    row.get("Name", ""),
                "id":      row.get("Id", ""),
                "version": row.get("Version", ""),
                "source":  row.get("Source", "winget"),
            })
    return candidates


def _brew_search(query: str) -> list[dict]:
    brew = shutil.which("brew")
    if not brew:
        return []
    rc, out, _ = _run([brew, "search", "--cask", query])
    if rc != 0:
        return []
    names = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith("=")]
    return [{"name": n, "id": n, "version": "", "source": "brew"} for n in names]


def _apt_search(query: str) -> list[dict]:
    rc, out, _ = _run(["apt-cache", "search", query])
    if rc != 0:
        return []
    candidates = []
    for line in out.splitlines():
        if " - " in line:
            pkg, desc = line.split(" - ", 1)
            candidates.append({"name": desc.strip(), "id": pkg.strip(), "version": "", "source": "apt"})
    return candidates


def _snap_search(query: str) -> list[dict]:
    rc, out, _ = _run(["snap", "find", query])
    if rc != 0:
        return []
    lines = [l for l in out.splitlines() if l.strip()][1:]  # skip header
    candidates = []
    for line in lines:
        parts = line.split()
        if parts:
            candidates.append({"name": parts[0], "id": parts[0], "version": parts[1] if len(parts) > 1 else "", "source": "snap"})
    return candidates


def _normalize_query_variant(query: str) -> Optional[str]:
    """
    Strips whitespace/punctuation and lowercases, e.g. "vs code" -> "vscode",
    "github desktop" -> "githubdesktop". Many vendors register exactly this
    squashed form as their winget Moniker (Microsoft's real VS Code package
    has moniker "vscode") or as the suffix of their package id — but
    `winget search`'s plain substring matching won't surface that moniker
    when the user's query has a space the moniker doesn't. Returns None if
    there's nothing to gain (query was already squashed, or empty).
    """
    lowered = query.lower().strip()
    squashed = re.sub(r"[^a-z0-9]", "", lowered)
    if squashed and squashed != lowered:
        return squashed
    return None


def _merge_candidates(*candidate_lists: list[dict]) -> list[dict]:
    """Dedup by package id (case-insensitive), preserving first-seen order."""
    seen: set[str] = set()
    merged: list[dict] = []
    for lst in candidate_lists:
        for c in lst:
            key = (c.get("id") or "").lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(c)
    return merged


def _search_with_variants(search_fn, query: str) -> list[dict]:
    """Runs search_fn against the raw query AND a squashed no-space variant
    (when it differs), merging results. Generic — not per-software."""
    primary = search_fn(query)
    variant = _normalize_query_variant(query)
    extra = search_fn(variant) if variant else []
    return _merge_candidates(primary, extra) if extra else primary


def _search_for_os(query: str, os_target: OperatingSystem) -> list[dict]:
    if os_target == OperatingSystem.WINDOWS:
        return _search_with_variants(_winget_search, query)
    if os_target == OperatingSystem.MACOS:
        return _search_with_variants(_brew_search, query)
    if os_target == OperatingSystem.LINUX:
        results = _search_with_variants(_apt_search, query)
        if not results:
            results = _search_with_variants(_snap_search, query)
        return results
    return []


# ---------------------------------------------------------------------------
# LLM disambiguation — chooses among REAL candidates, never invents an id
# ---------------------------------------------------------------------------

def _llm_pick_best_candidate(
    user_query: str, candidates: list[dict], os_target: OperatingSystem
) -> Optional[dict]:
    """
    Given the user's free-text software request and a list of candidates that
    actually came back from a real package-manager search, ask the LLM to
    choose the single best match. The LLM is constrained to choosing one of
    the provided candidates (by index) — it cannot fabricate a package id.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key or not candidates:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)

        listing = "\n".join(
            f"{i}: name={c['name']!r} id={c['id']!r} source={c.get('source', '')}"
            for i, c in enumerate(candidates[:25])
        )
        prompt = (
            f"A user asked to install/download software using this free-text request:\n"
            f'  "{user_query}"\n\n'
            f"Here are the REAL search results from the {os_target.value} package manager:\n"
            f"{listing}\n\n"
            "Pick the index of the candidate the user almost certainly meant (consider "
            "common abbreviations, misspellings, and brand names).\n\n"
            "IMPORTANT: when the user's wording is a well-known product name (e.g. "
            "\"VS Code\", \"Chrome\", \"Photoshop\"), strongly prefer the candidate that "
            "IS that actual flagship application over similarly-named companion tools, "
            "config helpers, launchers, extensions, plugins, or unofficial forks — even "
            "if those have a closer literal text match. The Id field's prefix before the "
            "first dot is usually the publisher (e.g. \"Microsoft.\", \"Google.\"); a "
            "well-known publisher matching the product name is a strong signal you've "
            "found the real thing, while names containing words like \"Config\", "
            "\"Helper\", \"Palette\", \"Launcher\", \"Extension\", or \"for VS Code\" "
            "usually indicate a companion tool, not the application itself.\n\n"
            "Reply with ONLY the index number. If none of the candidates plausibly "
            "match the user's actual request, reply with -1."
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        raw = response.choices[0].message.content.strip()
        m = re.search(r"-?\d+", raw)
        if not m:
            return None
        idx = int(m.group())
        if idx < 0 or idx >= len(candidates):
            return None
        logger.info(
            "[ResolverAgent] LLM disambiguated %r -> candidate #%d (%r)",
            user_query, idx, candidates[idx],
        )
        return candidates[idx]
    except Exception as exc:
        logger.warning("[ResolverAgent] LLM disambiguation failed: %s", exc)
        return None


def _llm_rewrite_query(user_query: str) -> Optional[str]:
    """
    When a search for the raw user query returns zero candidates, ask the LLM
    for a better search term (e.g. "chat gpd apk" -> "ChatGPT") and retry the
    search with that. Still 100% validated against real search results
    afterwards — this just improves the search query, it does not produce
    a package id directly.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)
        prompt = (
            f"A user wants to install or download a piece of software. Their request, "
            f"possibly containing speech-to-text errors, was:\n"
            f'  "{user_query}"\n\n'
            "What is the most likely PRODUCT NAME they mean? Reply with ONLY the "
            "product name (2-4 words max), suitable for searching a software package "
            "manager. No explanation, no quotes."
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
        )
        result = response.choices[0].message.content.strip().strip('"').strip("'")
        if len(result) < 2:
            return None
        return result
    except Exception as exc:
        logger.warning("[ResolverAgent] LLM query rewrite failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Redis cache (performance layer only — never a source of truth)
# ---------------------------------------------------------------------------

async def _cache_get(key: str) -> Optional[dict]:
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(str(settings.redis.url), decode_responses=True)
        raw = await r.get(key)
        await r.aclose()
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.debug("[ResolverAgent] cache get skipped: %s", exc)
        return None


async def _cache_set(key: str, value: dict) -> None:
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(str(settings.redis.url), decode_responses=True)
        await r.set(key, json.dumps(value), ex=CACHE_TTL_SEC)
        await r.aclose()
    except Exception as exc:
        logger.debug("[ResolverAgent] cache set skipped: %s", exc)


# ---------------------------------------------------------------------------
# SoftwareResolverAgent
# ---------------------------------------------------------------------------

class SoftwareResolverAgent:
    """
    Resolves a free-text software name -> a real, installable package id for
    the target OS. Fully dynamic: no hardcoded per-app dictionaries anywhere
    in this class. Ground truth always comes from a live package-manager
    search; the LLM is only ever used to (a) improve a bad search query or
    (b) pick the best match among real results.
    """

    async def resolve(self, software: str, os_target: OperatingSystem) -> ResolvedPackage:
        software = (software or "").strip()
        if not software:
            return ResolvedPackage(found=False, note="Empty software name")

        cache_key = f"voiceops:resolved_pkg:{os_target.value}:{software.lower()}"
        cached = await _cache_get(cache_key)
        if cached:
            logger.info("[ResolverAgent] Cache hit for %r (%s)", software, os_target.value)
            return ResolvedPackage(**cached, source="cache")

        t0 = time.perf_counter()

        # 1) Search with the raw user-provided term
        candidates = await asyncio.to_thread(_search_for_os, software, os_target)

        # 2) If nothing came back, ask the LLM to propose a cleaner search
        #    term (handles STT garbage / typos), then re-search with THAT.
        if not candidates:
            rewritten = await asyncio.to_thread(_llm_rewrite_query, software)
            if rewritten and rewritten.lower() != software.lower():
                logger.info("[ResolverAgent] Retrying search: %r -> %r", software, rewritten)
                candidates = await asyncio.to_thread(_search_for_os, rewritten, os_target)

        if not candidates:
            result = ResolvedPackage(
                found=False, source="none", candidates_considered=0,
                note=f"No package manager results for {software!r} on {os_target.value}",
            )
            return result

        chosen: Optional[dict] = None
        confidence = 0.0

        # 3) Exact (case-insensitive) name or id match -> trust directly, no LLM call needed
        sw_lower = software.lower()
        for c in candidates:
            if c["name"].lower() == sw_lower or c["id"].lower() == sw_lower:
                chosen, confidence = c, 0.99
                break

        # 4) Single unambiguous candidate -> use it
        if not chosen and len(candidates) == 1:
            chosen, confidence = candidates[0], 0.9

        # 5) Multiple candidates, no exact match -> let the LLM disambiguate
        #    among the REAL results (it cannot invent a new id)
        if not chosen:
            picked = await asyncio.to_thread(
                _llm_pick_best_candidate, software, candidates, os_target
            )
            if picked:
                chosen, confidence = picked, 0.8

        if not chosen:
            return ResolvedPackage(
                found=False, source="ambiguous", candidates_considered=len(candidates),
                note=f"{len(candidates)} candidates found but none could be confidently selected",
            )

        method = {
            OperatingSystem.WINDOWS: "winget_store" if _is_store_id(chosen["id"]) else "winget",
            OperatingSystem.MACOS:   "brew",
            OperatingSystem.LINUX:   chosen.get("source", "apt"),
        }.get(os_target, "winget")

        result = ResolvedPackage(
            found=True,
            package_id=chosen["id"],
            display_name=chosen["name"],
            source=chosen.get("source", "winget"),
            install_method=method,
            candidates_considered=len(candidates),
            confidence=confidence,
            note=f"Resolved via live {os_target.value} package search in {time.perf_counter() - t0:.1f}s",
        )

        await _cache_set(cache_key, {
            "found": result.found,
            "package_id": result.package_id,
            "display_name": result.display_name,
            "install_method": result.install_method,
            "candidates_considered": result.candidates_considered,
            "confidence": result.confidence,
            "note": result.note,
        })

        logger.info(
            "[ResolverAgent] %r (%s) -> id=%s method=%s conf=%.2f [%d candidates]",
            software, os_target.value, result.package_id, result.install_method,
            result.confidence, result.candidates_considered,
        )
        return result