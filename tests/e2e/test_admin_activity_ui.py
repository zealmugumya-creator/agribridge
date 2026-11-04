# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Browser tests for the admin console's Activity feed and its unseen-activity badge.

The complaint on 2026-11-04 was silence: people used the site and the console gave
no sign of it. The console now asks once a minute for the info events newer than the
last time Activity was opened, prints a number on the tab, and turns the person
beside an event into a link to that one person's timeline.

tests/test_activity.py pins the wiring in the source. What these run in headless
Chromium is the real static/admin.html, and what they pin is what the owner sees:
the number on the tab, the name beside an event, the request a click makes, and that
a string from the server stays a string. Every endpoint is stubbed — no production
row is read or written.
"""
from __future__ import annotations

import datetime
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
API = "https://agribridge-1-og7a.onrender.com"

SARAH = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
WHEN = "2026-11-04T09:00:00+00:00"

STATE: dict = {}
EVENTS: list = []          # every query the console sent to /api/admin/events
_counter = [0]


def _event(event_type, uid=None, details=None):
    _counter[0] += 1
    return {"id": f"e{_counter[0]}", "event_type": event_type, "severity": "info",
            "user_id": uid, "details": details or {}, "created_at": WHEN}


def _reset():
    _counter[0] = 0
    STATE.clear()
    STATE.update({
        "feed": [
            _event("session_started", SARAH, {"name": "Sarah Auma", "role": "farmer",
                                              "provider": "email"}),
            _event("activity.visit.first", None,
                   {"what": "Arrived on the site for the first time"}),
        ],
        "unseen": [],
        "per_user": {},
    })
    del EVENTS[:]


def _handle(route, request):
    cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
            "access-control-allow-methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS"}
    url = urlparse(request.url)
    host, path = url.netloc, url.path
    if host.startswith("127.0.0.1"):
        rel = unquote(path).lstrip("/") or "admin.html"
        target = (ROOT / "static" / rel).resolve()
        if not str(target).startswith(str((ROOT / "static").resolve())) or not target.is_file():
            return route.fulfill(status=404, body="not found")
        return route.fulfill(status=200, path=str(target),
                             content_type=mimetypes.guess_type(target.name)[0] or "text/plain")
    if request.method == "OPTIONS":
        return route.fulfill(status=204, headers=cors, body="")
    if host != urlparse(API).netloc:
        return route.fulfill(status=404, body="not stubbed")

    def send(payload, status=200):
        return route.fulfill(status=status, headers=dict(cors, **{"content-type": "application/json"}),
                             body=json.dumps(payload))

    if path == "/api/admin/events":
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        EVENTS.append(query)
        if "user_id" in query:
            return send(STATE["per_user"].get(query["user_id"], []))
        if "since" in query:
            return send(STATE["unseen"])
        return send(STATE["feed"])
    if path == "/api/admin/farmers":
        return send([{"id": SARAH, "full_name": "Sarah Auma", "district": "Kasese"},
                     {"id": OTHER, "full_name": "Allan Magembe", "district": "Jinja"}])
    return send([])


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
def console(browser, site):
    _reset()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              service_workers="block", bypass_csp=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    ctx.route("**/*", _handle)
    page.goto(site + "/admin.html", wait_until="domcontentloaded")
    # The sign-in form is not what these tests are about. startActPoll() is the
    # last thing a successful sign-in runs, so the badge starts there rather than
    # through showDash(), which would prefetch a dozen unrelated lists.
    page.evaluate("""() => {
      document.getElementById('login').classList.add('hidden');
      document.getElementById('dash').classList.remove('hidden');
      startActPoll();
    }""")
    yield page, errors
    ctx.close()


def _badge(page):
    return page.evaluate("document.getElementById('cnt_activity').textContent")


def _polls():
    return [q for q in EVENTS if "since" in q]


def _asked_after(page, count, predicate):
    """The console fires its own reads; a test can only wait for one of them to land."""
    for _ in range(40):
        if any(predicate(q) for q in EVENTS[count:]):
            return
        page.wait_for_timeout(100)
    pytest.fail(f"no request after #{count} matched; saw {EVENTS[count:]}")


def _open_feed(page):
    page.evaluate("() => load('farmers')")   # the Person column reads its names from there
    page.wait_for_function("(DATA.farmers || []).length === 2", timeout=8000)
    page.evaluate("showTab('activity')")
    page.wait_for_selector("#panel table tbody tr", timeout=8000)


def test_the_badge_counts_events_the_owner_has_not_seen_yet(console):
    page, errors = console
    STATE["unseen"] = [_event("session_started", SARAH, {"name": "Sarah Auma"}),
                       _event("activity.visit.first", None, {"what": "Arrived"})]
    page.wait_for_function("document.getElementById('cnt_activity').textContent === '2'",
                           timeout=8000)
    # A console opened for the first time asks for the last minute, not for every
    # row the table still holds — a badge of 200 tells the owner nothing.
    asked = datetime.datetime.fromisoformat(_polls()[0]["since"])
    assert _polls()[0]["severity"] == "info"
    gap = datetime.datetime.now(datetime.UTC) - asked
    assert datetime.timedelta(seconds=30) < gap < datetime.timedelta(minutes=2), _polls()[0]
    assert not errors, errors


def test_opening_the_feed_clears_the_badge_and_moves_the_marker_forward(console):
    page, _ = console
    STATE["unseen"] = [_event("session_started", SARAH, {"name": "Sarah Auma"})]
    page.wait_for_function("document.getElementById('cnt_activity').textContent === '1'",
                           timeout=8000)
    page.evaluate("showTab('activity')")
    assert _badge(page) == "", "opening the feed is how the owner says 'seen'"
    assert page.evaluate("localStorage.getItem('ab_admin_activity_seen')")

    before = len(_polls())
    page.evaluate("pollAct()")
    for _ in range(40):
        if len(_polls()) > before:
            break
        page.wait_for_timeout(100)
    polls = _polls()
    assert len(polls) > before, "the next poll never reached the server"
    assert polls[-1]["since"] > polls[0]["since"], "the next poll must start from when it was read"


def test_the_feed_prints_the_person_and_refuses_to_invent_one(console):
    page, errors = console
    _open_feed(page)
    text = page.inner_text("#panel")
    assert "session_started" in text
    assert "Sarah Auma" in text, "a signed-in event must name the person, not a bare id"
    assert "Arrived on the site for the first time" in text
    assert "—" in text, "an anonymous visit has no name and must say so rather than guess one"
    assert SARAH not in text, "the owner reads names, not uuids"
    assert not errors, errors


def test_clicking_a_person_asks_the_server_for_only_their_events(console):
    page, errors = console
    _open_feed(page)
    STATE["per_user"][SARAH] = [_event("session_started", SARAH, {"name": "Sarah Auma"})]

    asked = len(EVENTS)
    page.click("#panel button:text-is('Sarah Auma')")
    _asked_after(page, asked, lambda q: q.get("user_id") == SARAH)
    page.wait_for_function(
        "document.querySelector('#panel').innerText.includes('Timeline of')", timeout=8000)
    text = page.inner_text("#panel")
    assert "Timeline of" in text and "Sarah Auma" in text
    assert "Show everyone" in text

    STATE["feed"] = STATE["feed"][:1]
    asked = len(EVENTS)
    page.click("#panel button:text-is('Show everyone')")
    _asked_after(page, asked, lambda q: "user_id" not in q)
    page.wait_for_function(
        "document.querySelector('#panel').innerText.indexOf('Timeline of') === -1", timeout=8000)
    assert "Timeline of" not in page.inner_text("#panel")
    assert not errors, errors


def test_a_name_from_the_server_stays_a_name(console):
    page, errors = console
    STATE["feed"] = [_event("session_started", SARAH,
                            {"name": "<img src=x onerror=alert(1)>", "role": "farmer"})]
    _open_feed(page)
    assert page.locator("#panel img").count() == 0, "an event summary must not become markup"
    assert "<img src=x" in page.inner_text("#panel"), "…and must not be silently eaten either"
    assert not errors, errors


def test_the_badge_stops_counting_past_ninety_nine(console):
    page, _ = console
    STATE["unseen"] = [_event("activity.visit.first", None, {"what": "Arrived"})] * 120
    page.evaluate("pollAct()")
    page.wait_for_function("document.getElementById('cnt_activity').textContent === '99+'",
                           timeout=8000)


def test_signing_out_stops_the_console_asking_anything(console):
    page, errors = console
    page.wait_for_function("ACT_TIMER !== null", timeout=8000)  # the poll was actually running
    STATE["unseen"] = [_event("session_started", SARAH, {"name": "Sarah Auma"})]
    page.evaluate("showPerson('" + SARAH + "')")
    page.evaluate("logout()")
    assert page.evaluate("ACT_TIMER") is None, "a signed-out console holds no interval"
    assert page.evaluate("ACT_USER") is None
    assert _badge(page) == ""
    assert not errors, errors
