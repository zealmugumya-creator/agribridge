# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Phase 11: Multilingual unit tests.

Tests run purely in Python — no browser, no network.
They parse static/index.html directly and verify:
  1. Translation coverage (all active languages have key completeness thresholds)
  2. LANG_REGISTRY integrity (required fields, RTL flags, unique codes)
  3. Phase 10 locale helpers (fmtPrice / fmtNum / fmtDate) via Node.js subprocess
  4. AB_LOCALE map completeness (every LANG_REGISTRY code has a BCP-47 entry)
  5. Missing-key report (which keys are absent per language — printed, not failed)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "static" / "index.html"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _extract_en_keys(html: str) -> set[str]:
    """Return all keys defined in the English (_dict.en) block."""
    dict_start = html.find("var _dict = {")
    block = html[dict_start: dict_start + 90000]
    m = re.search(r"en:\s*\{(.*?)\},\s*lg:", block, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"'([a-z][a-zA-Z0-9_.]+)'\s*:\s*'", m.group(1)))


def _extract_lang_keys(html: str, code: str) -> set[str]:
    """Return all keys defined for the given language code."""
    dict_start = html.find("var _dict = {")
    block = html[dict_start: dict_start + 90000]
    pat = code + r":\s*\{(.*?)\}(?:,\n|\n\};)"
    m = re.search(pat, block, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r"'([a-z][a-zA-Z0-9_.]+)'\s*:\s*'", m.group(1)))


def _extract_lang_registry(html: str) -> list[dict]:
    """Parse _LANG_REGISTRY entries into a list of dicts."""
    m = re.search(r"var _LANG_REGISTRY\s*=\s*\[(.*?)\];", html, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    entries = []
    for entry in re.finditer(r"\{([^}]+)\}", block):
        props = {}
        for kv in re.finditer(r"(\w+)\s*:\s*'([^']*)'", entry.group(1)):
            props[kv.group(1)] = kv.group(2)
        for kv in re.finditer(r"(\w+)\s*:\s*(true|false)", entry.group(1)):
            props[kv.group(1)] = kv.group(2) == "true"
        entries.append(props)
    return entries


def _extract_locale_map(html: str) -> dict[str, str]:
    """Parse _LOCALE_MAP from Phase 10 block."""
    m = re.search(r"var _LOCALE_MAP\s*=\s*\{([^}]+)\}", html, re.DOTALL)
    if not m:
        return {}
    result = {}
    for kv in re.finditer(r"(\w+)\s*:\s*'([^']+)'", m.group(1)):
        result[kv.group(1)] = kv.group(2)
    return result


# ---------------------------------------------------------------------------
# Coverage thresholds — minimum % of English keys each language must have.
# Languages are at different stages; thresholds reflect the current state
# and must be raised as translations improve.
# ---------------------------------------------------------------------------
COVERAGE_THRESHOLDS: dict[str, int] = {
    "lg": 70,   # Luganda   — 73% currently; threshold ≥ 70
    "sw": 85,   # Swahili   — 91%
    "rw": 20,   # Kinyarwanda — 25%
    "fr": 85,   # French    — 90%
    "ar": 50,   # Arabic    — 55%
    "rk": 25,   # Rukiga    — 28%
    "rn": 15,   # Runyankole — 21%
    "ls": 15,   # Lusoga    — 20%
    "ac": 25,   # Acholi    — 28%
}

# Languages we consider "active" (have at least some translations)
ACTIVE_LANGS = set(COVERAGE_THRESHOLDS.keys())

# ---------------------------------------------------------------------------
# 1. Translation coverage
# ---------------------------------------------------------------------------

class TestTranslationCoverage:

    @pytest.fixture(scope="class")
    def html(self):
        return _html()

    @pytest.fixture(scope="class")
    def en_keys(self, html):
        keys = _extract_en_keys(html)
        assert len(keys) >= 50, "English dict appears incomplete — fewer than 50 keys found"
        return keys

    def test_english_has_core_keys(self, en_keys):
        core = {
            "nav.search", "nav.login", "nav.join",
            "login.title", "login.btn",
            "reg.title", "reg.btn",
            "set.title", "set.account",
        }
        missing = core - en_keys
        assert not missing, f"English missing core keys: {missing}"

    @pytest.mark.parametrize("lang_code,threshold", COVERAGE_THRESHOLDS.items())
    def test_coverage_above_threshold(self, html, en_keys, lang_code, threshold):
        lang_keys = _extract_lang_keys(html, lang_code)
        assert lang_keys, f"Language '{lang_code}' has no translation keys at all"
        pct = round(100 * len(lang_keys) / len(en_keys))
        assert pct >= threshold, (
            f"{lang_code}: coverage {pct}% is below threshold {threshold}%  "
            f"({len(lang_keys)}/{len(en_keys)} keys)"
        )

    @pytest.mark.parametrize("lang_code", sorted(ACTIVE_LANGS))
    def test_no_duplicate_keys(self, html, lang_code):
        """Each key should appear only once per language block."""
        dict_start = html.find("var _dict = {")
        block = html[dict_start: dict_start + 90000]
        pat = lang_code + r":\s*\{(.*?)\}(?:,\n|\n\};)"
        m = re.search(pat, block, re.DOTALL)
        if not m:
            pytest.skip(f"{lang_code} block not found")
        all_keys = re.findall(r"'([a-z][a-zA-Z0-9_.]+)'\s*:\s*'", m.group(1))
        seen: dict[str, int] = {}
        for k in all_keys:
            seen[k] = seen.get(k, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        assert not dupes, f"{lang_code} has duplicate keys: {dupes}"

    def test_missing_keys_report(self, html, en_keys, capsys):
        """Print a missing-key matrix — informational, not a failure."""
        print("\n=== Missing Key Report ===")
        print(f"{'Lang':<6} {'Keys':>5} {'Coverage':>9} {'Missing sample'}")
        print("-" * 70)
        for lc in sorted(ACTIVE_LANGS):
            lang_keys = _extract_lang_keys(html, lc)
            missing = sorted(en_keys - lang_keys)
            pct = round(100 * len(lang_keys) / len(en_keys)) if en_keys else 0
            sample = ", ".join(missing[:5])
            if len(missing) > 5:
                sample += f" … (+{len(missing)-5} more)"
            print(f"{lc:<6} {len(lang_keys):>5}/{len(en_keys)} {pct:>7}%   {sample}")
        # Always passes — this is a reporting test
        assert True


# ---------------------------------------------------------------------------
# 2. LANG_REGISTRY integrity
# ---------------------------------------------------------------------------

class TestLangRegistry:

    @pytest.fixture(scope="class")
    def registry(self):
        return _extract_lang_registry(_html())

    def test_registry_not_empty(self, registry):
        assert len(registry) >= 10, f"Expected ≥10 registry entries, got {len(registry)}"

    def test_required_fields(self, registry):
        required = {"code", "en", "native", "flag", "rtl", "status"}
        for entry in registry:
            missing = required - set(entry.keys())
            assert not missing, f"Entry {entry} missing fields: {missing}"

    def test_unique_codes(self, registry):
        codes = [e["code"] for e in registry]
        assert len(codes) == len(set(codes)), \
            f"Duplicate language codes: {[c for c in codes if codes.count(c) > 1]}"

    def test_english_is_first(self, registry):
        assert registry[0]["code"] == "en", \
            f"First registry entry should be 'en', got '{registry[0].get('code')}'"

    def test_rtl_flag_for_arabic(self, registry):
        ar = next((e for e in registry if e["code"] == "ar"), None)
        assert ar is not None, "Arabic (ar) not in LANG_REGISTRY"
        assert ar["rtl"] is True, "Arabic must have rtl:true"

    def test_rtl_false_for_latin_langs(self, registry):
        latin = ["en", "lg", "sw", "fr", "rw"]
        for entry in registry:
            if entry["code"] in latin:
                assert entry["rtl"] is False, \
                    f"{entry['code']} should have rtl:false, got rtl:{entry['rtl']}"

    def test_status_values_valid(self, registry):
        valid = {"full", "partial", "planned", "active"}
        for entry in registry:
            assert entry.get("status") in valid, \
                f"{entry['code']} has invalid status '{entry.get('status')}'"

    def test_flag_field_not_empty(self, registry):
        for entry in registry:
            assert entry.get("flag"), f"{entry['code']} has empty flag field"


# ---------------------------------------------------------------------------
# 3. Phase 10 locale helpers via Node.js
# ---------------------------------------------------------------------------

def _node_available() -> bool:
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _node_available(), reason="node not available")
class TestLocaleHelpers:
    """Run the Phase 10 JS in Node to verify outputs."""

    # Inject the Phase 10 script block into a minimal Node context
    @pytest.fixture(scope="class")
    def p10_js(self):
        html = _html()
        # Phase 10 is the LAST <script> block in index.html
        blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        if not blocks:
            pytest.skip("No script blocks found in index.html")
        last = blocks[-1]
        if "Phase 10" not in last:
            pytest.skip("Last script block does not contain Phase 10 code")
        return last

    def _run(self, p10_js: str, expr: str) -> str:
        """Evaluate expr after loading Phase 10 helpers; return stdout."""
        import tempfile, os
        # Provide browser stubs Node doesn't have.
        # Assign helpers to global so bare-name references work after window.X = fn().
        stubs = (
            "var localStorage = { _d:{}, getItem:function(k){return this._d[k]||null;}, setItem:function(k,v){this._d[k]=v;} };\n"
            "var document = { documentElement:{ dir:'ltr', lang:'en', classList:{ toggle:function(){}, add:function(){}, remove:function(){} }, setAttribute:function(){} }, head:{ appendChild:function(){} }, createElement:function(){ return { textContent:'', style:{}, id:'', getAttribute:function(){return null;} }; }, querySelectorAll:function(){return [];}, readyState:'complete', addEventListener:function(){} };\n"
            # In Node, 'this' at module level is {}, not global. Use globalThis so
            # window.fmtNum = fn registers on globalThis and is reachable as a bare name.
            "var window = globalThis;\n"
            "var _currentLang = 'en';\n"
            # Set both as a local var AND on globalThis (=window) so the IIFE's
            # `typeof _RTL_LANGS !== 'undefined'` branch fires AND window._RTL_LANGS
            # starts populated for AB_LOCALE.isRtl() to find.
            "var _RTL_LANGS = {ar:true}; globalThis._RTL_LANGS = _RTL_LANGS;\n"
        )
        # Strip box-drawing chars (U+2500-U+257F) from the extracted snippet
        # so Node -e inline eval doesn't choke on them
        import re as _re
        clean_js = _re.sub(r'[─-╿═-╬]+', '-', p10_js)
        script = stubs + "\n" + clean_js + "\n" + f"var __result = {expr}; process.stdout.write(String(__result));"
        # Write to temp file to avoid arg-list-too-long
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as tf:
            tf.write(script)
            tf_path = tf.name
        try:
            result = subprocess.run(
                ["node", tf_path],
                capture_output=True, text=True, timeout=10
            )
        finally:
            os.unlink(tf_path)
        if result.returncode != 0:
            pytest.fail(f"Node error: {result.stderr[:500]}")
        return result.stdout.strip()

    def test_fmt_num_basic(self, p10_js):
        result = self._run(p10_js, "fmtNum(1250000)")
        # Should produce 1,250,000 (comma thousands separator for en-UG)
        assert "1" in result and "250" in result, \
            f"fmtNum(1250000) unexpected: {result}"

    def test_fmt_price_millions(self, p10_js):
        result = self._run(p10_js, "fmtPrice(1200000)")
        assert "1.2M" in result or "1,200,000" in result, \
            f"fmtPrice(1200000) unexpected: {result}"
        assert "UGX" in result

    def test_fmt_price_with_unit(self, p10_js):
        result = self._run(p10_js, "fmtPrice(750, 'kg')")
        assert "UGX" in result and "kg" in result, \
            f"fmtPrice(750,'kg') unexpected: {result}"
        assert "750" in result

    def test_fmt_price_range(self, p10_js):
        result = self._run(p10_js, "fmtPriceRange(400, 700, 'kg')")
        assert "UGX" in result and "kg" in result, \
            f"fmtPriceRange unexpected: {result}"
        # Should contain both bounds
        assert "400" in result and "700" in result

    def test_fmt_price_short(self, p10_js):
        assert self._run(p10_js, "fmtPriceShort(1500000)") == "1.5M"
        assert self._run(p10_js, "fmtPriceShort(500000)") == "500K"

    def test_fmt_date_returns_string(self, p10_js):
        result = self._run(p10_js, "fmtDate('2024-11-23', 'short')")
        # Must contain some digits (the date)
        assert any(c.isdigit() for c in result), \
            f"fmtDate returned no digits: {result}"

    def test_fmt_date_medium(self, p10_js):
        result = self._run(p10_js, "fmtDate('2024-01-15', 'medium')")
        assert any(c.isdigit() for c in result)
        # Month abbreviation should appear for 'medium'
        assert len(result) > 4

    def test_fmt_num_decimal(self, p10_js):
        result = self._run(p10_js, "fmtNum(1250.75, 2)")
        assert "1" in result and "75" in result, \
            f"fmtNum decimal unexpected: {result}"

    def test_ab_locale_object(self, p10_js):
        result = self._run(p10_js, "typeof AB_LOCALE")
        assert result == "object", "AB_LOCALE should be an object"

    def test_ab_locale_is_rtl(self, p10_js):
        result = self._run(p10_js, "AB_LOCALE.isRtl('ar')")
        assert result == "true", "AB_LOCALE.isRtl('ar') should be true"
        result2 = self._run(p10_js, "AB_LOCALE.isRtl('en')")
        assert result2 == "false", "AB_LOCALE.isRtl('en') should be false"


# ---------------------------------------------------------------------------
# 4. AB_LOCALE map completeness
# ---------------------------------------------------------------------------

class TestLocaleMap:

    @pytest.fixture(scope="class")
    def html(self):
        return _html()

    @pytest.fixture(scope="class")
    def registry(self, html):
        return _extract_lang_registry(html)

    @pytest.fixture(scope="class")
    def locale_map(self, html):
        return _extract_locale_map(html)

    def test_locale_map_not_empty(self, locale_map):
        assert len(locale_map) >= 10, \
            f"_LOCALE_MAP has only {len(locale_map)} entries"

    def test_active_langs_in_locale_map(self, registry, locale_map):
        """Every language in LANG_REGISTRY should appear in _LOCALE_MAP."""
        reg_codes = {e["code"] for e in registry}
        missing = reg_codes - set(locale_map.keys())
        assert not missing, \
            f"Languages in LANG_REGISTRY but missing from _LOCALE_MAP: {missing}"

    def test_bcp47_format(self, locale_map):
        """Every locale value should look like a valid BCP-47 tag (xx or xx-XX)."""
        bcp47_pat = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")
        invalid = {k: v for k, v in locale_map.items() if not bcp47_pat.match(v)}
        assert not invalid, f"Invalid BCP-47 values in _LOCALE_MAP: {invalid}"

    def test_english_maps_to_ugandan_locale(self, locale_map):
        assert locale_map.get("en") == "en-UG", \
            f"English should map to en-UG for Ugandan conventions, got {locale_map.get('en')}"

    def test_arabic_maps_to_ar_locale(self, locale_map):
        ar = locale_map.get("ar", "")
        assert ar.startswith("ar"), f"Arabic should map to an ar-* locale, got {ar}"


# ---------------------------------------------------------------------------
# 5. JS syntax validation
# ---------------------------------------------------------------------------

class TestJsSyntax:

    @pytest.mark.skipif(not _node_available(), reason="node not available")
    def test_all_script_blocks_parse(self):
        """Every <script>...</script> block in index.html must parse without error."""
        import tempfile, os
        html = _html()
        blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        errors = []
        for i, block in enumerate(blocks, 1):
            script = f"new Function({json.dumps(block)})"
            with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as tf:
                tf.write(script)
                tf_path = tf.name
            try:
                r = subprocess.run(["node", tf_path], capture_output=True, text=True, timeout=10)
            finally:
                os.unlink(tf_path)
            if r.returncode != 0:
                first_err = r.stderr.strip().splitlines()[0] if r.stderr else "unknown"
                errors.append(f"Block {i}: {first_err}")
        assert not errors, "JS syntax errors found:\n" + "\n".join(errors)

    @pytest.mark.skipif(not _node_available(), reason="node not available")
    def test_admin_script_parses(self):
        """ab19ops.html script block must parse without error."""
        import tempfile, os
        ops_html = (ROOT / "static" / "ab19ops.html").read_text(encoding="utf-8")
        m = re.search(r"<script>([\s\S]*?)</script>\s*</body>", ops_html)
        if not m:
            pytest.skip("Could not find script block in ab19ops.html")
        script = f"new Function({json.dumps(m.group(1))})"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False, encoding='utf-8') as tf:
            tf.write(script)
            tf_path = tf.name
        try:
            r = subprocess.run(["node", tf_path], capture_output=True, text=True, timeout=15)
        finally:
            os.unlink(tf_path)
        assert r.returncode == 0, f"ab19ops.html JS error: {r.stderr.strip()[:300]}"
