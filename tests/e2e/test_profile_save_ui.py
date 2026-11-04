# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Browser tests for the profile editor in Settings → Account.

The owner's rule, 2026-11-04: nobody signs in with a fake name, a fake number or
a fake email. Migration 0008 put that rule in the database long ago — a
one-character name, a number that is not +256, and a second account on one phone
are all refused there. What the editor did not do was listen: it PATCHed
PostgREST directly, threw the answer away and toast-confirmed "Profile saved ✓".
A person whose number had just been claimed by another account was told the
write worked, and saw their own edited name on screen until the next reload.

These run the real static/index.html in headless Chromium against fixtures, and
pin what the person actually gets: the route's sentence on a refusal, the local
copy left alone when the write failed, "Profile saved ✓" only after a write that
answered, and one POST per tap now that a tap waits for the network.

No production data is read or written.
"""
from __future__ import annotations

import functools
import http.server
import json
import mimetypes
import threading
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]

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
    return { from: chain, storage: { from: chain }, channel: chain, removeChannel: function(){},
      auth: {
        onAuthStateChange: function(cb){ listeners.push(cb); return {data:{subscription:{unsubscribe:function(){}}}}; },
        getSession: async function(){ return {data:{session: window.__sb.session}}; },
        refreshSession: async function(){ return {data:{session: window.__sb.session}}; },
        signInWithPassword: async function(){ return {data:{},error:{message:'stubbed'}}; },
        signUp: async function(){ return {data:{},error:{message:'stubbed'}}; },
        signInWithOAuth: async function(){ return {data:{},error:{message:'stubbed'}}; },
        updateUser: async function(){ return {data:{},error:null}; },
        signOut: async function(){ window.__sb.emit('SIGNED_OUT', null); return {error:null}; },
        resetPasswordForEmail: async function(){ return {data:{},error:null}; },
        resend: async function(){ return {data:{},error:null}; }
      } };
  } };
})();
"""

ME = "4f1e2b3c-5d6a-4b7c-8d9e-0f1a2b3c4d5e"
SAVED = {"name": "Sarah Auma", "phone": "+256771234567", "district": "Kasese"}

SC: dict = {}
POSTS: list = []      # every body sent to /api/user/profile
WRITES: list = []     # every write the browser attempted straight on PostgREST


def _reset():
    SC.clear()
    # The answer /api/user/profile gives this run, and whether it can be reached.
    SC["status"] = 200
    SC["body"] = {"ok": True}
    SC["unreachable"] = False
    del POSTS[:]
    del WRITES[:]


def _handle(route, request):
    cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
            "access-control-allow-methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS",
            "access-control-expose-headers": "content-range"}
    url = urlparse(request.url)
    host, path, method = url.netloc, url.path, request.method

    if host.startswith("127.0.0.1"):
        rel = unquote(path).lstrip("/") or "index.html"
        target = (ROOT / "static" / rel).resolve()
        if not str(target).startswith(str((ROOT / "static").resolve())) or not target.is_file():
            return route.fulfill(status=404, body="not found")
        return route.fulfill(status=200, path=str(target),
                             content_type=mimetypes.guess_type(target.name)[0] or "text/plain")
    if method == "OPTIONS":
        return route.fulfill(status=204, headers=cors, body="")
    if "jsdelivr.net" in host and "supabase" in request.url:
        return route.fulfill(status=200, content_type="application/javascript", body=SUPABASE_STUB)

    if host.endswith("supabase.co"):
        if method in ("POST", "PATCH", "DELETE", "PUT"):
            WRITES.append((method, path.rsplit("/", 1)[-1]))
        q = parse_qs(url.query)
        if path.endswith("/auth/v1/settings"):
            return _send(route, cors, 200, {"external": {"google": False, "email": True}})
        if q.get("select") == ["count"]:
            return route.fulfill(status=200, headers={**cors, "content-range": "0-0/0"},
                                 content_type="application/json", body="[]")
        return _send(route, cors, 200, [])

    if host.endswith("onrender.com"):
        if path == "/api/user/profile":
            POSTS.append(json.loads(request.post_data or "{}"))
            if SC["unreachable"]:
                return route.abort()
            return _send(route, cors, SC["status"], SC["body"])
        # Everything else the app asks for on boot: an empty, honest answer.
        return _send(route, cors, 200, {})

    return route.abort()


def _send(route, cors, status, payload):
    return route.fulfill(status=status, headers=dict(cors, **{"content-type": "application/json"}),
                         body=json.dumps(payload))


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "static"))
    handler.log_message = lambda *a, **k: None
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
                pytest.skip(f"chromium unavailable: {chromium_error}; Chrome fallback: {chrome_error}")
        yield b
        b.close()


@pytest.fixture
def account(browser, site):
    """Signed in as a farmer whose row already holds SAVED, on Settings → Account."""
    _reset()
    ctx = browser.new_context(viewport={"width": 390, "height": 844},
                              service_workers="block", bypass_csp=True)
    page = ctx.new_page()
    ctx.route("**/*", _handle)
    page.goto(site + "/index.html", wait_until="domcontentloaded")
    page.wait_for_function("typeof sbAuth !== 'undefined' && sbAuth", timeout=10000)
    page.evaluate("try{localStorage.setItem('ab_seen_splash','1')}catch(e){}")
    page.evaluate("enterApp(); hideSplash()")
    page.evaluate("""(uid) => {
      window.__sb.emit('SIGNED_IN', {access_token:'tok', user:{
        id:uid, email:'sarah@example.ug',
        user_metadata:{name:'Sarah Auma', role:'farmer',
                       phone:'+256771234567', district:'Kasese'}}});
    }""", ME)
    page.wait_for_function("typeof currentUser!=='undefined' && currentUser && currentUser.supaId",
                           timeout=8000)
    page.evaluate("openModal('settingsModal'); settingsGo('account')")
    page.wait_for_selector("#setName", timeout=5000)
    _watch_toasts(page)
    assert page.evaluate("currentUser.name") == SAVED["name"]
    yield page
    ctx.close()


# ── the editor, driven from the page rather than from the source ─────────────
def _watch_toasts(page):
    """Record every toast the app shows.

    Reading `#toast`.textContent is a race: the node holds only the most recent
    message and clears itself after 3.2 s, so a welcome toast that lands late can
    answer for a save that was never reported. The list says what was actually
    said, in order.
    """
    page.evaluate("""() => {
      window.__toasts = [];
      var _show = window.showToast;
      window.showToast = function(m){ window.__toasts.push(String(m)); return _show.apply(this, arguments); };
    }""")
    page.evaluate("var t=document.getElementById('toast');"
                  "if(t){t.classList.remove('show'); t.textContent='';}")


def _fill(page, name=None, phone=None, district=None):
    """Type into a box. A box left out of this call keeps the value it was rendered with."""
    for box, value in (("setName", name), ("setPhone", phone), ("setDistrict", district)):
        if value is not None:
            page.fill(f"#{box}", value)


def _tap_save(page, times=1):
    page.evaluate("""(n) => {
      var box = document.getElementById('settingsModal');
      var button = [].slice.call(box.querySelectorAll('button'))
        .find(function(b){ return b.textContent.trim() === 'Save changes'; });
      for (var i = 0; i < n; i++) button.click();   // two taps in one task: no answer can land between them
    }""", times)


def _await_toast(page, want, timeout_ms=8000):
    """Wait until a toast containing `want` has been shown; return everything shown."""
    page.wait_for_function(
        "(want) => window.__toasts.some(function(t){ return t.indexOf(want) >= 0; })",
        arg=want, timeout=timeout_ms)
    return page.evaluate("window.__toasts.slice()")


def _answered(page, timeout_ms=8000):
    """Wait for the POST to reach the backend. A route handler only runs while the
    page is being pumped, so this waits through Playwright, not through time.sleep."""
    deadline = timeout_ms / 100
    while deadline and not POSTS:
        deadline -= 1
        page.wait_for_timeout(100)
    return bool(POSTS)


# ── a refusal reaches the person, in words ───────────────────────────────────
def test_a_number_another_account_holds_is_not_reported_as_saved(account):
    page = account
    refusal = "That phone number is already registered to another account"
    SC["status"] = 409
    SC["body"] = {"error": refusal}
    _fill(page, phone="+256700111222")
    _tap_save(page)
    assert _answered(page)
    shown = _await_toast(page, "already registered")
    assert refusal in shown, "the person is told which rule the number broke"
    assert "Profile saved ✓" not in shown, (
        "this is the tick that used to appear while the database refused the number")
    assert page.evaluate("currentUser.phone") == SAVED["phone"], (
        "a refused number must not be kept on screen as if the database held it")
    assert page.evaluate("JSON.parse(localStorage.getItem('ab_user')||'{}').phone") == SAVED["phone"]
    assert page.evaluate("document.getElementById('settingsModal').classList.contains('open')"), (
        "the screen stays open so the number can be corrected")


@pytest.mark.parametrize("status, body, want", [
    (401, {"error": "Missing token"}, "Sign in again"),
    (502, {"error": "Could not save profile"}, "Could not save profile"),
])
def test_an_answer_that_is_not_a_saved_is_never_shown_as_one(account, status, body, want):
    # The 401 body is the word "Missing token" and abApi's stand-in for a dead
    # connection is the word "network"; neither is a sentence a farmer can act on.
    page = account
    SC["status"] = status
    SC["body"] = body
    _tap_save(page)
    assert _answered(page)
    assert want in " | ".join(_await_toast(page, want))


def test_a_connection_that_failed_says_so_rather_than_the_word_network(account):
    page = account
    SC["unreachable"] = True
    _fill(page, name="Sarah Nakato")
    _tap_save(page)
    shown = _await_toast(page, "could not reach AgriBridge")
    assert "We could not reach AgriBridge. Check your connection and try again." in shown
    assert "Profile saved ✓" not in shown, "a dead connection is not a saved profile"
    assert page.evaluate("currentUser.name") == SAVED["name"]


# ── a write that answered is reported as one ─────────────────────────────────
def test_the_stored_number_is_shown_in_the_box_that_edits_it(account):
    page = account
    # The 🇺🇬+256 flag beside this box carries the country code, so the box itself
    # holds the national number. The pattern that split the stored E.164 value
    # used a greedy \d+ and took the whole number with it, leaving every farmer
    # facing an empty box that saved as '+256' — a code with no number after it.
    assert page.input_value("#setPhone") == "771234567"
    _tap_save(page)
    assert _answered(page)
    assert POSTS[0]["phone"] == SAVED["phone"]


def test_a_save_that_worked_sends_the_backend_the_edited_values(account):
    page = account
    _fill(page, name="Sarah Auma", phone="0700 111 222", district="Ibanda")
    _tap_save(page)
    assert _answered(page)
    assert POSTS[0] == {"full_name": "Sarah Auma", "phone": "+256700111222", "district": "Ibanda"}, (
        "the box beside the flag holds the national number; the component joins "
        "them before anything is sent, so the backend only ever sees E.164")
    assert "Profile saved ✓" in _await_toast(page, "Profile saved")
    assert page.evaluate("currentUser.district") == "Ibanda"
    assert page.evaluate("JSON.parse(localStorage.getItem('ab_user')||'{}').phone") == "+256700111222"
    assert not page.evaluate("document.getElementById('settingsModal').classList.contains('open')")


def test_a_box_left_empty_means_leave_that_field_alone(account):
    page = account
    _fill(page, name="Sarah Auma", phone="", district="")
    _tap_save(page)
    assert _answered(page)
    assert POSTS[0] == {"full_name": "Sarah Auma", "phone": SAVED["phone"],
                        "district": SAVED["district"]}, (
        "an empty box must not clear a column the person never meant to touch")
    assert "Profile saved ✓" in _await_toast(page, "Profile saved")


def test_one_tap_sends_one_write_while_the_first_is_still_waiting(account):
    page = account
    _tap_save(page, times=2)
    assert _answered(page)
    page.wait_for_timeout(1000)
    assert len(POSTS) == 1, f"one profile edit was written {len(POSTS)} times"
    assert "Profile saved ✓" in _await_toast(page, "Profile saved")


def test_the_editor_writes_through_the_route_and_never_straight_to_the_table(account):
    # Migration 0024 takes the direct PATCH away in the database; this pins that
    # no client code is left which would have needed it.
    page = account
    _fill(page, name="Sarah Auma", district="Ibanda")
    del WRITES[:]
    _tap_save(page)
    assert _answered(page)
    page.wait_for_timeout(500)
    assert "Profile saved ✓" in _await_toast(page, "Profile saved")
    assert WRITES == [], f"the browser wrote directly on PostgREST: {WRITES}"
