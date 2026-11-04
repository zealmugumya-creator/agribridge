# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Who did what: activity events carry a verified user_id, and only the
moments worth a line in a timeline are accepted.

The owner's complaint on 2026-11-04 was that they could not tell who had used
AgriBridge. The events already existed — eight of about sixty named a person,
so a timeline grouped by user had holes for everything else. These tests pin the
attribution, the short whitelist of browser-side events, and the rule that a
press is not an event.
"""
from __future__ import annotations

import pytest

import app as app_mod


class _Rows(list):
    """A list that can also carry how many times GoTrue was asked who called."""

    def __init__(self):
        super().__init__()
        self.calls = []


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture
def rows(monkeypatch):
    """Every row _log_system_event tries to write, plus the count of GoTrue lookups."""
    captured, calls = _Rows(), []
    monkeypatch.setattr(app_mod, "supa_insert",
                        lambda table, row, *a, **k: captured.append((table, row)) or {"id": "1"})

    def _verify():
        calls.append(1)
        app_mod.g.auth_user = {"id": "u-7"}
        return "u-7", None

    monkeypatch.setattr(app_mod, "verify_supabase_user", _verify)
    captured.calls = calls
    return captured


def _event(client, body, ip, token=None):
    headers = {"Authorization": "Bearer t"} if token else {}
    return client.post("/api/activity", json=body, headers=headers,
                       environ_overrides={"REMOTE_ADDR": ip})


# ── attribution ───────────────────────────────────────────────────────────────
def test_an_event_names_the_person_whose_token_sent_it(rows):
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": "Bearer tok"}):
        app_mod._log_system_event("listing_updated_web", {"id": "L1"}, severity="info")
    table, row = rows[0]
    assert table == "system_events"
    assert row["user_id"] == "u-7"


def test_the_identity_lookup_costs_one_request_not_one_event(rows):
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": "Bearer tok"}):
        app_mod._log_system_event("a", {})
        app_mod._log_system_event("b", {})
        app_mod._log_system_event("c", {})
    assert len(rows) == 3
    assert len(rows.calls) == 1, "a page load must not pay one GoTrue round trip per event"


def test_an_event_for_someone_else_keeps_that_person(rows):
    # Admin actions are recorded against the account they affected, not the
    # admin whose token happened to be in the header.
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": "Bearer tok"}):
        app_mod._log_system_event("order_admin_approved", {}, user_id="farmer-3")
    assert rows[0][1]["user_id"] == "farmer-3"
    assert rows.calls == []


def test_anonymous_activity_is_recorded_but_not_attributed_to_anyone(rows):
    with app_mod.app.test_request_context("/api/x"):
        app_mod._log_system_event("activity.visit.first", {})
    assert rows[0][1]["user_id"] is None
    assert rows.calls == [], "no token means no auth round trip on the request's path"


def test_the_background_worker_still_logs_without_a_request(rows):
    app_mod._log_system_event("outbox_retry_failed", {}, severity="warning")
    assert rows[0][1]["user_id"] is None
    assert rows[0][1]["severity"] == "warning"


def test_a_broken_auth_check_cannot_stop_an_event_being_written(rows, monkeypatch):
    def _boom():
        raise RuntimeError("GoTrue unreachable")
    monkeypatch.setattr(app_mod, "verify_supabase_user", _boom)
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": "Bearer tok"}):
        app_mod._log_system_event("payment_mark_paid_failed", {})
    assert rows[0][1]["user_id"] is None


def test_an_admin_action_does_not_ask_GoTrue_about_its_own_token(rows):
    # Admins authenticate with a token this backend signed. Sending it to GoTrue
    # returns 401 after a round trip on the request's critical path, and Render
    # is not fast enough to give that away for every approval.
    token = app_mod.make_token({"role": "admin"})
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": f"Bearer {token}"}):
        assert app_mod.verify_token(required_role="admin")[1] is None
        app_mod._log_system_event("order_admin_approved", {}, severity="info")
    assert rows[0][1]["user_id"] is None
    assert rows.calls == []


# ── the browser-side events ──────────────────────────────────────────────────
def test_a_first_visit_becomes_one_line_on_the_feed(client, rows):
    r = _event(client, {"event": "visit.first"}, ip="10.9.0.1")
    assert r.status_code == 200
    assert rows[0][1]["event_type"] == "activity.visit.first"
    assert rows[0][1]["severity"] == "info"


def test_a_signed_in_visit_lands_on_that_persons_timeline(client, rows):
    _event(client, {"event": "visit.first"}, ip="10.9.0.2", token=True)
    assert rows[0][1]["user_id"] == "u-7"


def test_presses_are_not_events(client, rows):
    for name in ("button.click", "page.view", "keystroke", "", "visit.second"):
        assert _event(client, {"event": name}, ip=f"10.9.1.{abs(hash(name)) % 200}").status_code == 400
    assert rows == [], "anything outside the whitelist must never reach the table"


def test_a_body_that_is_not_json_is_refused(client, rows):
    r = client.post("/api/activity", data="not json",
                    headers={"Content-Type": "text/plain"},
                    environ_overrides={"REMOTE_ADDR": "10.9.2.1"})
    assert r.status_code == 400
    assert rows == []


def test_the_spa_reports_the_visit_it_cannot_be_seen_in_any_other_way():
    from pathlib import Path
    repo = Path(app_mod.__file__).resolve().parent
    html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    start = html.index("if (!localStorage.getItem('ab_visited'))")
    block = html[start:start + 900]
    assert "USSD_API+'/api/activity'" in block and "event:'visit.first'" in block, (
        "the first visit is the one arrival no backend route can witness")
    assert ".catch(function(){});" in block, (
        "a beacon that fails must never surface as an error to a first-time visitor")


# ── reading it back: one person, or only what is new ─────────────────────────
UUID = "4f1e2b3c-5d6a-4b7c-8d9e-0f1a2b3c4d5e"


@pytest.fixture
def asked(monkeypatch):
    """What /api/admin/events actually asked Supabase for."""
    seen = {}

    def fake_get(table, filters=None, limit=100):
        seen["table"], seen["filters"], seen["limit"] = table, filters, limit
        return []

    monkeypatch.setattr(app_mod, "supa_get", fake_get)
    return seen


def _events(client, query):
    return client.get("/api/admin/events" + query, headers={
        "Authorization": "Bearer " + app_mod.make_token({"role": "admin"})})


def test_a_persons_timeline_is_their_own_rows(client, asked):
    assert _events(client, f"?user_id={UUID}").status_code == 200
    assert asked["table"] == "system_events"
    assert asked["filters"]["user_id"] == f"eq.{UUID}"


def test_a_bad_user_id_is_refused_rather_than_reading_as_an_empty_timeline(client, asked):
    for bad in ("1", "not-a-uuid", "4f1e2b3c-5d6a-4b7c-8d9e-0f1a2b3c4d5%;select=1"):
        assert _events(client, f"?user_id={bad}").status_code == 400
    assert asked == {}, "a refused query must never reach Supabase"


def test_the_bell_asks_for_only_what_is_new(client, asked):
    r = _events(client, "?severity=info&since=2026-11-04T09:00:00.500Z")
    assert r.status_code == 200
    assert asked["filters"]["created_at"] == "gt.2026-11-04T09:00:00.500Z"
    assert asked["filters"]["severity"] == "eq.info"
    assert asked["filters"]["order"] == "created_at.desc"


def test_a_since_that_is_not_a_time_is_refused(client, asked):
    for bad in ("last tuesday", "2026-11-04T99:99", "1970"):
        assert _events(client, f"?since={bad}").status_code == 400
    assert asked == {}


def test_the_events_endpoint_still_needs_an_admin(client):
    assert client.get("/api/admin/events").status_code == 401
    farmer = client.get(f"/api/admin/events?user_id={UUID}", headers={
        "Authorization": "Bearer " + app_mod.make_token({"role": "farmer"})})
    assert farmer.status_code == 403


# ── the owner's side: a badge that means "new", and a feed that filters ──────
@pytest.fixture(scope="module")
def admin_js():
    from pathlib import Path
    repo = Path(app_mod.__file__).resolve().parent
    return (repo / "static" / "admin.html").read_text(encoding="utf-8")


def _fn(src, name):
    """One function's own text — assertions on a whole 100 KB file pass even when
    the wiring they describe is in the wrong place."""
    start = src.index("function " + name + "(")
    return src[start:src.index("\n  }", start) + 4]


def test_the_activity_tab_can_show_an_unread_count(admin_js):
    assert 'id="cnt_activity"' in admin_js


def test_the_badge_counts_what_is_new_not_what_is_old(admin_js):
    poll = _fn(admin_js, "pollAct")
    assert "/api/admin/events?severity=info&since=" in poll
    since = _fn(admin_js, "actSince")
    assert "ACT_SEEN='ab_admin_activity_seen'" in admin_js
    assert "localStorage.getItem(ACT_SEEN)" in since
    assert "localStorage.setItem(ACT_SEEN" in _fn(admin_js, "actMarkSeen"), (
        "seen-ness has to survive a reload of the console")
    assert "Date.now()-ACT_POLL_MS" in since, (
        "a console that has never been opened must not report history as news")
    assert "99+" in _fn(admin_js, "paintAct")


def test_opening_the_feed_is_what_marks_it_seen(admin_js):
    assert "if(t==='activity') actMarkSeen();" in _fn(admin_js, "showTab")


def test_a_console_nobody_is_looking_at_asks_the_server_nothing(admin_js):
    assert "if(document.hidden) return;" in _fn(admin_js, "pollAct")
    assert "startActPoll()" in _fn(admin_js, "showDash"), "the bell starts with the dashboard"
    logout = _fn(admin_js, "logout")
    assert "stopActPoll()" in logout and "ACT_USER=null" in logout, (
        "a signed-out console must keep polling nothing")


def test_a_person_on_the_feed_is_a_link_to_their_timeline(admin_js):
    person = _fn(admin_js, "showPerson")
    assert "UUID_RE.test(String(uid))" in person, (
        "an id from the page goes into a query string; anything malformed is dropped")
    assert "user_id=" in _fn(admin_js, "load")
    assert "onclick=\"showPerson(" in _fn(admin_js, "renderActivity")


def test_names_and_details_are_escaped_before_they_reach_the_page(admin_js):
    render = _fn(admin_js, "renderActivity")
    assert "h(summary)" in render and "h(personName(ev.user_id))" in render
