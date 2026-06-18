"""
Patches app/agents/resolver_agent/agent.py with two fixes:

1. _search_for_os now also searches a squashed no-space variant of the
   query (e.g. "vs code" -> "vscode") and merges results, so vendor
   monikers like Microsoft's real "vscode" moniker get found even when
   the user's wording has a space the moniker doesn't.

2. The LLM disambiguation prompt now explicitly warns against picking
   companion tools / config helpers / extensions over the actual
   well-known flagship application.

Run from the voiceops project root:
    python patch_resolver_agent.py
"""
import pathlib

PATH = pathlib.Path("app/agents/resolver_agent/agent.py")
text = PATH.read_text()

# --- Fix 1: broaden search coverage ---
old1 = '''def _search_for_os(query: str, os_target: OperatingSystem) -> list[dict]:
    if os_target == OperatingSystem.WINDOWS:
        return _winget_search(query)
    if os_target == OperatingSystem.MACOS:
        return _brew_search(query)
    if os_target == OperatingSystem.LINUX:
        results = _apt_search(query)
        return results if results else _snap_search(query)
    return []'''

new1 = '''def _normalize_query_variant(query: str) -> "Optional[str]":
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


def _merge_candidates(*candidate_lists: "list[dict]") -> "list[dict]":
    """Dedup by package id (case-insensitive), preserving first-seen order."""
    seen: set = set()
    merged: list = []
    for lst in candidate_lists:
        for c in lst:
            key = (c.get("id") or "").lower()
            if key and key not in seen:
                seen.add(key)
                merged.append(c)
    return merged


def _search_with_variants(search_fn, query: str) -> "list[dict]":
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
    return []'''

assert old1 in text, "Fix 1 pattern not found — has resolver_agent.py already been patched, or modified manually?"
text = text.replace(old1, new1)

# --- Fix 2: strengthen the LLM disambiguation prompt ---
old2 = '''        prompt = (
            f"A user asked to install/download software using this free-text request:\\n"
            f'  "{user_query}"\\n\\n'
            f"Here are the REAL search results from the {os_target.value} package manager:\\n"
            f"{listing}\\n\\n"
            "Pick the index of the single candidate that best matches what the user "
            "almost certainly meant (consider common abbreviations, misspellings and "
            "brand names). Reply with ONLY the index number. If none of the candidates "
            "plausibly match, reply with -1."
        )'''

new2 = '''        prompt = (
            f"A user asked to install/download software using this free-text request:\\n"
            f'  "{user_query}"\\n\\n'
            f"Here are the REAL search results from the {os_target.value} package manager:\\n"
            f"{listing}\\n\\n"
            "Pick the index of the candidate the user almost certainly meant (consider "
            "common abbreviations, misspellings, and brand names).\\n\\n"
            "IMPORTANT: when the user's wording is a well-known product name (e.g. "
            "\\"VS Code\\", \\"Chrome\\", \\"Photoshop\\"), strongly prefer the candidate that "
            "IS that actual flagship application over similarly-named companion tools, "
            "config helpers, launchers, extensions, plugins, or unofficial forks — even "
            "if those have a closer literal text match. The Id field's prefix before the "
            "first dot is usually the publisher (e.g. \\"Microsoft.\\", \\"Google.\\"); a "
            "well-known publisher matching the product name is a strong signal you've "
            "found the real thing, while names containing words like \\"Config\\", "
            "\\"Helper\\", \\"Palette\\", \\"Launcher\\", \\"Extension\\", or \\"for VS Code\\" "
            "usually indicate a companion tool, not the application itself.\\n\\n"
            "Reply with ONLY the index number. If none of the candidates plausibly "
            "match the user's actual request, reply with -1."
        )'''

assert old2 in text, "Fix 2 pattern not found — has resolver_agent.py already been patched, or modified manually?"
text = text.replace(old2, new2)

PATH.write_text(text)
print("Patched OK — both fixes applied to", PATH)