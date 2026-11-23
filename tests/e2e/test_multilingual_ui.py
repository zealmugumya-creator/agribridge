# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Phase 11: Multilingual browser regression tests.

Runs static/index.html in headless Chromium and verifies:
  1. Language switcher works for every active language (no JS errors)
  2. RTL layout is applied for Arabic (dir="rtl" on <html>)
  3. LTR is restored when switching away from Arabic
  4. fmtPrice / fmtNum / fmtDate helpers are exposed on window
  5. AB_LOCALE.isRtl() returns expected values
  6. setLang() persists choice to localStorage
  7. Admin panel (ab19ops.html) Language Centre tab renders without errors
"""

from __future__ import annotations

import functools
import http.server
import json
import mimetypes
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Supabase stub — same pattern as the other e2e tests
# ---------------------------------------------------------------------------

SUPABASE_STUB = r"""
(function(){
  var listeners = [];
  window.__sb = { session: null };
  window.__sb.emit = function(event, session){
    window.__sb.session = session;
    listeners.forEach(function(cb){ cb(event, session); });
  };
  function chain(){ return new Proxy(function(){}, { get(_, k){
    if(k==='then') return function(res){ res({data:[],error:null}); };
    return chain();
  }, apply(){ return chain(); } }); }
  window.supabase = { createClient: function(){
    return {
      auth: {
        getSession: function(){ return Promise.resolve({data:{session:null}}); },
        onAuthStateChange: function(cb){
          listeners.push(cb);
          return { data:{ subscription:{ unsubscribe:function(){} } } };
        },
        signInWithPassword: function(){ return Promise.resolve({data:{user:null,session:null},error:null}); },
        signUp: function(){ return Promise.resolve({data:{user:null,session:null},error:null}); },
        signOut: function(){ return Promise.resolve({error:null}); }
      },
      from: chain,
      storage: { from: chain }
    };
  }};
})();
"""

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site():
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(ROOT / "static"),
    )
    handler.log_message = lambda *a, **k: None  # type: ignore[method-assign]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as chromium_error:
            try:
                b = p.chromium.launch(channel="chrome")
            except Exception as chrome_error:
                pytest.skip(
                    f"chromium unavailable: {chromium_error}; Chrome: {chrome_error}"
                )
        yield b
        b.close()


def _make_context(browser, site: str):
    """Create a fresh browser context for one test."""
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844},
        service_workers="block",
        bypass_csp=True,
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    def _supabase(route, request):
        cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*"}
        route.fulfill(status=200, headers=cors, content_type="application/json", body="[]")

    # Route the Supabase JS SDK to our stub
    for pat in ["**/npm/@supabase/**", "**/supabase-js*"]:
        ctx.route(pat, lambda r, _: r.fulfill(
            status=200, content_type="application/javascript", body=SUPABASE_STUB
        ))
    ctx.route("https://*.supabase.co/**", _supabase)

    def _other(route, request):
        host = urlparse(request.url).netloc
        if host.startswith("127.0.0.1"):
            rel = unquote(urlparse(request.url).path).lstrip("/") or "index.html"
            target = (ROOT / "static" / rel).resolve()
            static_root = (ROOT / "static").resolve()
            if not str(target).startswith(str(static_root)):
                return route.fulfill(status=404, body="not found")
            if target.is_file():
                ct = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return route.fulfill(status=200, path=str(target), content_type=ct)
            return route.fulfill(status=404, body="not found")
        route.abort()

    ctx.route("**/*", _other)
    return ctx, page, errors


def _load_index(page, site: str) -> None:
    """Load index.html and wait for the app shell to be ready."""
    page.goto(site + "/index.html", wait_until="domcontentloaded")
    page.wait_for_function(
        "typeof sbAuth !== 'undefined' && sbAuth",
        timeout=12000, polling=100,
    )
    # Dismiss splash, enter app
    page.evaluate("try{localStorage.setItem('ab_seen_splash','1')}catch(e){}")
    page.evaluate("if(typeof enterApp==='function') enterApp(); if(typeof hideSplash==='function') hideSplash();")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLanguageSwitcher:

    # Languages with enough translation to usefully test
    ACTIVE_LANGS = [
        ("en", False, "English"),
        ("lg", False, "Luganda"),
        ("sw", False, "Kiswahili"),
        ("fr", False, "French"),
        ("ar", True,  "Arabic"),
        ("rw", False, "Kinyarwanda"),
    ]

    @pytest.fixture
    def env(self, browser, site):
        ctx, page, errors = _make_context(browser, site)
        _load_index(page, site)
        yield page, errors
        ctx.close()

    @pytest.mark.parametrize("code,expect_rtl,label", ACTIVE_LANGS)
    def test_set_lang_no_js_errors(self, env, code, expect_rtl, label):
        """Calling setLang(code) must not raise a JS error."""
        page, errors = env
        errors.clear()
        page.evaluate(f"setLang({json.dumps(code)})")
        page.wait_for_timeout(300)
        js_errors = [e for e in errors if "setLang" in e or "undefined" in e.lower()]
        assert not js_errors, f"JS errors after setLang('{code}'): {js_errors}"

    @pytest.mark.parametrize("code,expect_rtl,label", ACTIVE_LANGS)
    def test_set_lang_persists_to_localstorage(self, env, code, expect_rtl, label):
        page, _ = env
        page.evaluate(f"setLang({json.dumps(code)})")
        page.wait_for_timeout(200)
        saved = page.evaluate("localStorage.getItem('ab_lang')")
        assert saved == code, f"Expected '{code}' in localStorage, got '{saved}'"

    def test_arabic_sets_rtl(self, env):
        page, errors = env
        errors.clear()
        page.evaluate("setLang('ar')")
        page.wait_for_timeout(400)
        direction = page.evaluate("document.documentElement.dir")
        assert direction == "rtl", \
            f"Expected dir='rtl' after setLang('ar'), got '{direction}'"

    def test_switch_from_arabic_restores_ltr(self, env):
        page, errors = env
        page.evaluate("setLang('ar')")
        page.wait_for_timeout(200)
        page.evaluate("setLang('en')")
        page.wait_for_timeout(300)
        direction = page.evaluate("document.documentElement.dir")
        assert direction == "ltr", \
            f"Expected dir='ltr' after switching back to English, got '{direction}'"

    def test_rtl_layout_class_applied(self, env):
        page, _ = env
        page.evaluate("setLang('ar')")
        page.wait_for_timeout(300)
        has_class = page.evaluate(
            "document.documentElement.classList.contains('rtl-layout')"
        )
        assert has_class, "Expected .rtl-layout class on <html> for Arabic"

    def test_ltr_layout_class_after_switch(self, env):
        page, _ = env
        page.evaluate("setLang('ar')")
        page.wait_for_timeout(200)
        page.evaluate("setLang('sw')")
        page.wait_for_timeout(300)
        has_rtl = page.evaluate(
            "document.documentElement.classList.contains('rtl-layout')"
        )
        assert not has_rtl, ".rtl-layout should be removed after switching to Swahili"

    def test_english_has_ltr_on_load(self, browser, site):
        """Fresh page load with 'en' in localStorage → dir='ltr' by default."""
        ctx, page, errors = _make_context(browser, site)
        try:
            page.goto(site + "/index.html", wait_until="domcontentloaded")
            page.evaluate("localStorage.setItem('ab_lang','en')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(500)
            direction = page.evaluate("document.documentElement.dir || 'ltr'")
            assert direction in ("ltr", ""), f"Expected ltr, got '{direction}'"
        finally:
            ctx.close()

    def test_arabic_rtl_persists_after_reload(self, browser, site):
        """If 'ar' is saved, the page should load in RTL mode."""
        ctx, page, errors = _make_context(browser, site)
        try:
            page.goto(site + "/index.html", wait_until="domcontentloaded")
            page.evaluate("localStorage.setItem('ab_lang','ar')")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            direction = page.evaluate("document.documentElement.dir")
            assert direction == "rtl", \
                f"Arabic RTL should persist across reload, got '{direction}'"
        finally:
            ctx.close()


class TestLocaleHelpers:
    """Verify Phase 10 helpers are exposed on window inside the browser."""

    @pytest.fixture(scope="class")
    def env(self, browser, site):
        ctx, page, errors = _make_context(browser, site)
        _load_index(page, site)
        yield page, errors
        ctx.close()

    def test_fmt_num_exposed(self, env):
        page, _ = env
        t = page.evaluate("typeof fmtNum")
        assert t == "function", f"fmtNum should be a function, got {t}"

    def test_fmt_price_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtPrice") == "function"

    def test_fmt_price_range_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtPriceRange") == "function"

    def test_fmt_price_short_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtPriceShort") == "function"

    def test_fmt_date_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtDate") == "function"

    def test_fmt_time_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtTime") == "function"

    def test_fmt_rel_time_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof fmtRelTime") == "function"

    def test_ab_locale_exposed(self, env):
        page, _ = env
        assert page.evaluate("typeof AB_LOCALE") == "object"

    def test_fmt_price_ugx_prefix(self, env):
        page, _ = env
        result = page.evaluate("fmtPrice(15000, 'kg')")
        assert "UGX" in result, f"fmtPrice should include UGX, got: {result}"
        assert "kg" in result

    def test_fmt_price_millions_abbreviated(self, env):
        page, _ = env
        result = page.evaluate("fmtPrice(1200000)")
        assert "UGX" in result
        # Should abbreviate to M
        assert "M" in result or "1,200,000" in result, \
            f"Expected abbreviation for 1.2M, got: {result}"

    def test_fmt_price_range_contains_both_values(self, env):
        page, _ = env
        result = page.evaluate("fmtPriceRange(400, 700, 'kg')")
        assert "400" in result and "700" in result, \
            f"Range should contain both values: {result}"

    def test_fmt_price_short_1m(self, env):
        page, _ = env
        result = page.evaluate("fmtPriceShort(1500000)")
        assert result == "1.5M", f"Expected '1.5M', got '{result}'"

    def test_fmt_price_short_500k(self, env):
        page, _ = env
        result = page.evaluate("fmtPriceShort(500000)")
        assert result == "500K", f"Expected '500K', got '{result}'"

    def test_fmt_num_thousands(self, env):
        page, _ = env
        result = page.evaluate("fmtNum(1250000)")
        # Must contain '1' and '250' and '000' somewhere
        digits = result.replace(",", "").replace(".", "").replace(" ", "")
        assert digits == "1250000", f"fmtNum(1250000) garbled: {result}"

    def test_fmt_date_returns_non_empty(self, env):
        page, _ = env
        result = page.evaluate("fmtDate('2024-11-23', 'short')")
        assert result and len(result) > 2, f"fmtDate returned empty: '{result}'"
        assert any(c.isdigit() for c in result)

    def test_ab_locale_is_rtl(self, env):
        page, _ = env
        assert page.evaluate("AB_LOCALE.isRtl('ar')") is True
        assert page.evaluate("AB_LOCALE.isRtl('en')") is False
        assert page.evaluate("AB_LOCALE.isRtl('sw')") is False

    def test_ab_locale_bcp47_callable(self, env):
        page, _ = env
        result = page.evaluate("AB_LOCALE.bcp47()")
        assert isinstance(result, str) and len(result) >= 2, \
            f"AB_LOCALE.bcp47() should return a string, got: {result}"


class TestAdminLanguageCentre:
    """Verify the Phase 9 Language Centre tab in ab19ops.html."""

    @pytest.fixture(scope="class")
    def admin_env(self, browser, site):
        ctx, page, errors = _make_context(browser, site)
        page.goto(site + "/ab19ops.html", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        yield page, errors
        ctx.close()

    def test_page_loads_without_js_errors(self, admin_env):
        _, errors = admin_env
        critical = [e for e in errors if "SyntaxError" in e or "TypeError" in e]
        assert not critical, f"Critical JS errors on admin page: {critical}"

    def test_language_centre_tab_exists(self, admin_env):
        page, _ = admin_env
        tab = page.locator(".tab[data-tab='languages']")
        assert tab.count() == 1, "Language Centre tab not found in sidebar"

    def test_language_centre_tab_clickable(self, admin_env):
        page, errors = admin_env
        errors.clear()
        tab = page.locator(".tab[data-tab='languages']")
        tab.click()
        page.wait_for_timeout(500)
        # langView should be visible
        lang_view = page.locator("#langView")
        assert lang_view.count() == 1
        hidden = page.evaluate(
            "document.getElementById('langView').classList.contains('hidden')"
        )
        assert not hidden, "#langView should not be hidden after clicking the tab"

    def test_language_centre_renders_content(self, admin_env):
        page, errors = admin_env
        # Navigate to the tab
        page.locator(".tab[data-tab='languages']").click()
        page.wait_for_timeout(600)
        # langView should have some content
        content = page.evaluate(
            "document.getElementById('langView').innerHTML.length"
        )
        assert content > 100, \
            f"langView appears empty after renderLanguageCentre() (innerHTML len={content})"

    def test_no_new_errors_after_tab_click(self, admin_env):
        page, errors = admin_env
        errors.clear()
        page.locator(".tab[data-tab='languages']").click()
        page.wait_for_timeout(600)
        syntax_errors = [e for e in errors if "SyntaxError" in e]
        assert not syntax_errors, \
            f"JS SyntaxErrors triggered by Language Centre tab: {syntax_errors}"

    def test_lang_registry_accessible_in_admin(self, admin_env):
        page, _ = admin_env
        count = page.evaluate("typeof LANG_REGISTRY !== 'undefined' && LANG_REGISTRY.length")
        assert count and int(count) >= 10, \
            f"LANG_REGISTRY not accessible or too small in admin panel: {count}"


class TestNoJsErrors:
    """Smoke test: load the main page in each language and collect any JS errors."""

    LANGS = ["en", "lg", "sw", "fr", "ar", "rw", "rk", "ac"]

    @pytest.mark.parametrize("lang", LANGS)
    def test_no_critical_errors_per_language(self, browser, site):
        ctx, page, errors = _make_context(browser, site)
        try:
            page.evaluate(f"localStorage.setItem('ab_lang', {json.dumps(lang)})")
            page.goto(site + "/index.html", wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            critical = [
                e for e in errors
                if "SyntaxError" in e or "ReferenceError" in e
            ]
            assert not critical, \
                f"Critical JS errors with lang='{lang}': {critical}"
        finally:
            ctx.close()
