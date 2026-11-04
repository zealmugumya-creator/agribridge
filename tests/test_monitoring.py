# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Supabase table webhook -> Discord/system_events, admin inbox, logistics gate."""
from __future__ import annotations

from pathlib import Path

import pytest

import app as app_mod

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(app_mod, "SUPABASE_WEBHOOK_SECRET", "s3cret")
    sent, logged = [], []
    monkeypatch.setattr(app_mod, "_discord_notify", lambda **k: sent.append(k))
    monkeypatch.setattr(app_mod, "_log_system_event", lambda *a, **k: logged.append((a, k)))
    return sent, logged


def _hook(client, body, key="s3cret"):
    return client.post("/api/hooks/supabase", json=body, headers={"X-Webhook-Secret": key})


def test_hook_rejects_missing_or_wrong_secret(client, secret):
    assert client.post("/api/hooks/supabase", json={}).status_code == 401
    assert _hook(client, {}, key="nope").status_code == 401


def test_hook_disabled_until_secret_is_configured(client, monkeypatch):
    monkeypatch.setattr(app_mod, "SUPABASE_WEBHOOK_SECRET", "")
    assert _hook(client, {}, key="").status_code == 401


def test_insert_goes_to_discord_and_activity_feed_without_pii(client, secret):
    sent, logged = secret
    r = _hook(client, {"type": "INSERT", "table": "contact_messages", "record": {
        "id": "1", "subject": "Help", "message": "x" * 500, "email": "a@b.com", "phone": "0771",
        "password": "pw"}})
    assert r.status_code == 200
    assert sent[0]["event_type"] == "support.contact_message"
    body = sent[0]["description"]
    assert "Help" in body and "a@b.com" not in body and "0771" not in body and "pw" not in body
    assert len(logged) == 1


def test_fraud_flag_is_critical(client, secret):
    sent, _ = secret
    _hook(client, {"type": "INSERT", "table": "fraud_flags", "record": {"id": "9", "reason": "dup"}})
    assert sent[0]["severity"] == "critical"


def test_unwatched_table_and_non_insert_are_ignored(client, secret):
    sent, _ = secret
    assert _hook(client, {"type": "INSERT", "table": "password_reset_tokens", "record": {"token": "t"}}).get_json()["ignored"]
    assert _hook(client, {"type": "UPDATE", "table": "payouts", "record": {}}).get_json()["ignored"]
    assert sent == []


def test_inbox_requires_admin_and_whitelists_tables(client, monkeypatch):
    assert client.get("/api/admin/inbox").status_code == 401
    h = {"Authorization": "Bearer " + app_mod.make_token({"role": "admin"})}
    assert client.get("/api/admin/inbox?table=password_reset_tokens", headers=h).status_code == 400
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [{"id": "1", "token": "x", "subject": "hi"}])
    rows = client.get("/api/admin/inbox?table=contact_messages", headers=h).get_json()
    assert rows == [{"id": "1", "subject": "hi"}]          # secrets stripped


def test_logistics_is_gated_and_links_back_to_real_admin_page():
    html = (REPO / "static/logistics.html").read_text(encoding="utf-8")
    assert 'href="/admin.html"' in html and "/static/admin.html" not in html
    assert 'id="gate"' in html and "/api/admin/verify" in html
    assert "localStorage.getItem('ab_admin_token')" not in html   # shares admin's session instead


# ── POST /api/contact: the owner's first-party alert path ────────────────────
# Before this, the form wrote straight into Supabase from the browser, so the
# only alert was a manually installed Database Webhook. With the trigger or
# SUPABASE_WEBHOOK_SECRET absent, a message was stored and the owner heard
# nothing (reported 2026-11-04). Each test uses its own client IP: the limiter
# falls back to an in-process bucket when Redis is absent, as it is here.
GOOD = {"full_name": "Grace Nakato", "contact": "0771 234567", "role": "Farmer",
        "message": "Can I sell my tomatoes to hotels in Kampala?"}


@pytest.fixture
def contact(monkeypatch):
    stored, sent, logged = [], [], []
    monkeypatch.setattr(app_mod, "supa_insert",
                        lambda table, data: stored.append((table, data)) or [{"id": "m1"}])
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: (None, None))
    monkeypatch.setattr(app_mod, "_discord_notify", lambda **k: sent.append(k) or True)
    monkeypatch.setattr(app_mod, "_log_system_event", lambda *a, **k: logged.append((a, k)))
    return stored, sent, logged


def _send(client, contact, body, ip="10.1.0.1"):
    return client.post("/api/contact", json=body, environ_overrides={"REMOTE_ADDR": ip})


def test_a_contact_message_is_stored_and_alerts_the_owner(client, contact):
    stored, sent, logged = contact
    r = _send(client, contact, GOOD)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert stored[0][0] == "contact_messages"
    assert stored[0][1]["full_name"] == "Grace Nakato"
    assert sent[0]["event_type"] == "support.contact_message"
    assert logged[0][0][0] == "support.contact_message"


def test_the_alert_carries_the_question_but_no_contact_details(client, contact):
    _stored, sent, logged = contact
    _send(client, contact, {**GOOD, "contact": "grace@example.com"}, ip="10.1.0.2")
    assert "hotels" in sent[0]["description"]
    assert "grace@example.com" not in sent[0]["description"]
    assert "grace@example.com" not in str(logged)


def test_contact_needs_a_reachable_answer(client, contact):
    stored, sent, _logged = contact
    r = _send(client, contact, {**GOOD, "contact": "call me"}, ip="10.1.0.3")
    assert r.status_code == 400
    assert stored == [] and sent == []
    assert "reply to" in r.get_json()["error"]


def test_contact_accepts_an_email_or_a_phone(client, contact):
    stored, _sent, _logged = contact
    ok = [_send(client, contact, {**GOOD, "contact": "grace@example.com"}, ip="10.1.0.4"),
          _send(client, contact, {**GOOD, "contact": "+256771234567"}, ip="10.1.0.5")]
    assert [r.status_code for r in ok] == [200, 200]
    assert len(stored) == 2


def test_a_message_that_cannot_be_stored_is_reported_not_faked(client, contact, monkeypatch):
    stored, sent, logged = contact
    monkeypatch.setattr(app_mod, "supa_insert", lambda table, data: None)
    r = _send(client, contact, GOOD, ip="10.1.0.6")
    assert r.status_code == 502
    assert stored == [] and sent == []
    assert logged[0][0][0] == "contact_message_save_failed"


def test_the_role_a_visitor_picks_is_stored_as_written(client, contact):
    # The select offers "Hotel / Restaurant"; a whitelist would silently rewrite it.
    stored, _sent, _logged = contact
    _send(client, contact, {**GOOD, "role": "Hotel / Restaurant"}, ip="10.1.0.7")
    assert stored[0][1]["role"] == "Hotel / Restaurant"


def test_a_signed_in_sender_is_attributed_to_their_account(client, contact, monkeypatch):
    _stored, _sent, logged = contact
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: ("u-9", None))
    _send(client, contact, GOOD, ip="10.1.0.8")
    assert logged[0][1]["user_id"] == "u-9"


def test_the_form_uses_the_backend_and_keeps_the_old_insert_as_fallback():
    js = (REPO / "static/index.html").read_text(encoding="utf-8")
    start = js.index("async function sendContactMsgCore()")
    body = js[start:start + 1800]
    assert "USSD_API+'/api/contact'" in body, "the form still bypasses the backend"
    assert "status === 0 || status >= 500" in body, (
        "the direct insert must stay as a fallback, but only when the backend "
        "is unreachable or failing - never after a validation rejection")
