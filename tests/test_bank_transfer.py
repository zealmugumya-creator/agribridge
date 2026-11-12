# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Bank transfer checkout: always offered, account details only when the owner
configured them, orders stay unpaid until an admin verifies against a bank
reference. Also covers the once-per-account signup alert. Network and DB faked."""
from __future__ import annotations

import datetime

import pytest

import app as app_mod


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


def _admin_headers():
    token = app_mod.make_token({"sub": "admin", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


# ── Providers / config ────────────────────────────────────────────────────────
def test_bank_is_always_offline_available(client, monkeypatch):
    monkeypatch.setattr(app_mod, "BANK_TRANSFER_ACCOUNTS", [])
    prov = client.get("/api/pay/providers").get_json()["providers"]
    assert prov["bank"] is True


def test_providers_expose_configured_accounts_only(client, monkeypatch):
    accounts = [{"bank": "DFCU", "account_name": "AgriBridge Ltd",
                 "account_number": "1234567890", "branch": "Main"}]
    monkeypatch.setattr(app_mod, "BANK_TRANSFER_ACCOUNTS", accounts)
    body = client.get("/api/pay/providers").get_json()
    assert body["bank_accounts"] == accounts


def test_no_hardcoded_bank_account_in_source():
    import re
    from pathlib import Path
    repo = Path(app_mod.__file__).resolve().parent
    html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    # A fabricated account is a standalone number carrying the old 9030 prefix.
    # A hex image-hash substring (e.g. a89030c2….jpg) is not an account number,
    # so anchor on a non-word boundary instead of a blunt "9030" substring.
    account = re.search(r"(?<!\w)9030(?: ?\d){3,}(?!\w)", html)
    assert account is None, f"hardcoded bank account: {account.group(0)}"
    assert "Stanbic" not in html


# ── Initiate is a no-op for bank ──────────────────────────────────────────────
def test_initiate_bank_returns_offline_mode(client, monkeypatch):
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: ("user-1", None))
    r = client.post("/api/pay/initiate",
                    json={"provider": "bank", "order_ref": "AB-1", "amount": 5000})
    assert r.status_code == 200
    assert r.get_json()["mode"] == "bank"


# ── Admin verification ────────────────────────────────────────────────────────
def _order_rows(monkeypatch, target, checkout):
    def fake_get(table, filters=None, limit=100):
        if table != "orders":
            return []
        if filters and "id" in filters:
            return target
        return checkout
    monkeypatch.setattr(app_mod, "supa_get", fake_get)


def test_verify_bank_requires_admin(client):
    assert client.post("/api/admin/orders/o1/verify-bank-transfer",
                       json={"bank_reference": "TRX-123"}).status_code == 401


def test_verify_bank_rejects_short_reference(client):
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "x"}, headers=_admin_headers())
    assert r.status_code == 400
    assert r.get_json()["code"] == "bank_reference_required"


def test_verify_bank_refuses_non_bank_order(client, monkeypatch):
    _order_rows(monkeypatch, [{"id": "o1", "payment_ref": "AB-1", "payment_method": "momo",
                               "payment_status": "unpaid", "status": "pending"}], [])
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "TRX-123"}, headers=_admin_headers())
    assert r.status_code == 409
    assert r.get_json()["code"] == "not_a_bank_order"


def test_verify_bank_refuses_cancelled_order(client, monkeypatch):
    _order_rows(monkeypatch, [{"id": "o1", "payment_ref": "AB-1", "payment_method": "bank",
                               "payment_status": "unpaid", "status": "cancelled"}], [])
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "TRX-123"}, headers=_admin_headers())
    assert r.status_code == 409
    assert r.get_json()["code"] == "order_cancelled"


def test_verify_bank_marks_paid_with_stored_amount(client, monkeypatch):
    target = [{"id": "o1", "payment_ref": "AB-1", "payment_method": "bank",
               "payment_status": "unpaid", "status": "pending"}]
    checkout = [{"id": "o1", "total_price": 40000, "total_amount": None,
                 "quantity_kg": None, "price_per_kg": None,
                 "status": "pending", "payment_status": "unpaid"}]
    _order_rows(monkeypatch, target, checkout)
    calls = {}

    def fake_mark(tx_ref, **kw):
        calls.update(tx_ref=tx_ref, **kw)
        return True

    monkeypatch.setattr(app_mod, "_mark_checkout_paid", fake_mark)
    monkeypatch.setattr(app_mod, "_log_system_event", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "supa_insert", lambda *a, **k: None)
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "TRX-9001"}, headers=_admin_headers())
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert calls["tx_ref"] == "AB-1" and calls["provider"] == "bank"
    assert calls["provider_ref"] == "TRX-9001"
    assert int(calls["received"]) == 40000          # amount from the order, not the caller


def test_verify_bank_reports_already_paid(client, monkeypatch):
    target = [{"id": "o1", "payment_ref": "AB-1", "payment_method": "bank",
               "payment_status": "paid", "status": "delivered"}]
    checkout = [{"id": "o1", "total_price": 40000, "total_amount": None,
                 "quantity_kg": None, "price_per_kg": None,
                 "status": "delivered", "payment_status": "paid"}]
    _order_rows(monkeypatch, target, checkout)
    monkeypatch.setattr(app_mod, "_mark_checkout_paid", lambda *a, **k: True)
    monkeypatch.setattr(app_mod, "_log_system_event", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "supa_insert", lambda *a, **k: None)
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "TRX-9001"}, headers=_admin_headers())
    assert r.status_code == 200 and r.get_json()["already_paid"] is True


def test_verify_bank_failure_is_not_success(client, monkeypatch):
    target = [{"id": "o1", "payment_ref": "AB-1", "payment_method": "bank",
               "payment_status": "unpaid", "status": "pending"}]
    checkout = [{"id": "o1", "total_price": 40000, "total_amount": None,
                 "quantity_kg": None, "price_per_kg": None,
                 "status": "pending", "payment_status": "unpaid"}]
    _order_rows(monkeypatch, target, checkout)
    monkeypatch.setattr(app_mod, "_mark_checkout_paid", lambda *a, **k: False)
    events = []
    monkeypatch.setattr(app_mod, "_log_system_event",
                        lambda et, d, **k: events.append(et))
    r = client.post("/api/admin/orders/o1/verify-bank-transfer",
                    json={"bank_reference": "TRX-9001"}, headers=_admin_headers())
    assert r.status_code == 503
    assert "bank_verify_failed" in events


# ── Signup alert fires once ───────────────────────────────────────────────────
class _Res:
    def __init__(self, ok=True, data=None):
        self.ok, self.data, self.error, self.status_code = ok, data or {}, None, 200


def _sync(monkeypatch, *, marker_present, created_at, meta=None, farmer_row=None,
          session_alerted_today=False):
    auth_user = {"user_metadata": {"full_name": "Z"} if meta is None else meta,
                 "created_at": created_at}

    # verify_supabase_user normally populates g.auth_user inside the request
    # context; mirror that here (patching g from outside a context raises).
    def _verify():
        app_mod.g.auth_user = auth_user
        return ("u1", None)

    monkeypatch.setattr(app_mod, "verify_supabase_user", _verify)
    monkeypatch.setattr(app_mod, "_legal_role", lambda uid: ("farmer", None))
    monkeypatch.setattr(app_mod, "LEGAL_SERVICE",
                        type("L", (), {"sync_signup": lambda *a, **k: _Res(data={}),
                                       "status": lambda *a, **k: _Res(data={})})())

    def fake_get(table, filters=None, limit=100):
        if table == "system_events":
            # The handler asks two different questions of the same table: has
            # this account ever been announced, and has this person been
            # announced today. Answer them separately or one gates the other.
            wanted = str((filters or {}).get("event_type") or "")
            if wanted.endswith("signup_alerted"):
                return [{"id": "e1"}] if marker_present else []
            return [{"id": "e1"}] if session_alerted_today else []
        if table == "farmers":
            return [farmer_row] if farmer_row else []
        return []

    monkeypatch.setattr(app_mod, "supa_get", fake_get)
    logged, notified = [], []
    monkeypatch.setattr(app_mod, "_log_system_event",
                        lambda et, d, **k: logged.append(et))
    monkeypatch.setattr(app_mod, "_discord_notify", lambda *a, **k: notified.append(k))
    return logged, notified


def _fired(notified, event_type):
    """The alerts sent for one event type — a session ping must not disturb the
    signup assertions, and neither should a listing alert."""
    return [n for n in notified if n.get("event_type") == event_type]


def test_signup_alerts_once_for_a_new_account(monkeypatch):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    logged, notified = _sync(monkeypatch, marker_present=False, created_at=now)
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        assert c.post("/api/legal/sync-signup").status_code == 200
    assert "signup_alerted" in logged and len(_fired(notified, "user.signup")) == 1


def test_signup_alert_suppressed_when_marker_exists(monkeypatch):
    now = datetime.datetime.now(datetime.UTC).isoformat()
    logged, notified = _sync(monkeypatch, marker_present=True, created_at=now)
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        assert c.post("/api/legal/sync-signup").status_code == 200
    assert "signup_alerted" not in logged and _fired(notified, "user.signup") == []


def test_signup_alert_suppressed_for_old_account(monkeypatch):
    old = (datetime.datetime.now(datetime.UTC)
           - datetime.timedelta(days=30)).isoformat()
    logged, notified = _sync(monkeypatch, marker_present=False, created_at=old)
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        assert c.post("/api/legal/sync-signup").status_code == 200
    assert _fired(notified, "user.signup") == []


# ── The signup alert must show the real name ─────────────────────────────────
# Live evidence (2026-10-23, #signups): every email/password account arrived as
# "Name —" because sign-up stores user_metadata.name while the alert read
# full_name. A dash on an ops channel reads as a broken account, not as a
# missing field, so the alert now resolves one name from every source.
def _now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def _alert_description(monkeypatch, **kw):
    logged, notified = _sync(monkeypatch, marker_present=False, created_at=_now(), **kw)
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        assert c.post("/api/legal/sync-signup").status_code == 200
    signup = _fired(notified, "user.signup")
    assert len(signup) == 1
    return signup[0]["description"]


def test_signup_alert_names_a_password_account_by_the_name_it_stored(monkeypatch):
    # This is the shape sign-up actually writes: metadata.name, no full_name.
    description = _alert_description(monkeypatch, meta={"name": "Allan Magembe"})
    assert "Allan Magembe" in description
    assert "—" not in description


def test_signup_alert_falls_back_to_the_profile_row(monkeypatch):
    description = _alert_description(monkeypatch, meta={},
                                     farmer_row={"full_name": "Sarah Auma"})
    assert "Sarah Auma" in description


def test_signup_alert_says_so_when_no_name_was_ever_given(monkeypatch):
    description = _alert_description(monkeypatch, meta={"name": "   "})
    assert "not given at sign-up" in description
    assert "—" not in description


def test_signup_alert_names_the_phone_when_one_was_given(monkeypatch):
    # The owner calls people back from this message, so the number is the one that
    # can be dialled, and it appears only when the person actually gave one — a
    # placeholder dash is what the two tests above forbid for the whole message.
    description = _alert_description(monkeypatch,
                                     meta={"name": "Allan Magembe", "phone": "0771 234 567"})
    assert "+256771234567" in description
    assert "—" not in description


def test_signup_stores_the_name_under_both_metadata_keys():
    from pathlib import Path
    repo = Path(app_mod.__file__).resolve().parent
    html = (repo / "static" / "index.html").read_text(encoding="utf-8")
    assert "options: { data: { name: name, full_name: name," in html, (
        "sign-up must write both metadata keys, or the alert depends on a DB read")


# ── A sign-in is an event the owner can see ───────────────────────────────────
# Signing in happens in the browser against GoTrue, so the backend saw nothing
# at all until this legal check — which every session runs — started recording.
def _signed_in(monkeypatch, *, session_alerted_today=False, meta=None, provider="google"):
    """One sign-in: returns (events recorded, alerts sent)."""
    _sync(monkeypatch, marker_present=True, created_at=_now(),
          session_alerted_today=session_alerted_today)   # marker on: no signup alert

    def _verify():
        app_mod.g.auth_user = {"user_metadata": meta if meta is not None else {},
                               "created_at": _now(), "app_metadata": {"provider": provider}}
        return ("u1", None)

    monkeypatch.setattr(app_mod, "verify_supabase_user", _verify)
    detailed, notified = [], []
    monkeypatch.setattr(app_mod, "_log_system_event",
                        lambda et, d, **k: detailed.append((et, d, k)))
    monkeypatch.setattr(app_mod, "_discord_notify", lambda *a, **k: notified.append(k))
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        assert c.post("/api/legal/sync-signup").status_code == 200
    return detailed, notified


def test_a_sign_in_is_recorded_with_the_name_and_how_they_arrived(monkeypatch):
    detailed, _ = _signed_in(monkeypatch, meta={"full_name": "Sarah Auma"})
    started = [e for e in detailed if e[0] == "session_started"]
    assert len(started) == 1
    _event, details, kwargs = started[0]
    assert details == {"role": "farmer", "name": "Sarah Auma", "provider": "google"}
    assert kwargs["user_id"] == "u1" and kwargs["severity"] == "info"


def test_a_person_is_announced_once_a_day_not_once_a_page_load(monkeypatch):
    # The endpoint behind this runs on every session start, and a browser fires
    # it again on every reload — an unthrottled ping would bury #signups.
    _detailed, notified = _signed_in(monkeypatch, meta={"name": "Allan"})
    assert len(_fired(notified, "user.signin")) == 1
    assert "Allan" in _fired(notified, "user.signin")[0]["description"]

    _detailed, already = _signed_in(monkeypatch, session_alerted_today=True)
    assert _fired(already, "user.signin") == []
    assert "session_alerted" not in [e[0] for e in _detailed]


def test_the_sign_in_ping_carries_no_contact_details(monkeypatch):
    _detailed, notified = _signed_in(
        monkeypatch, meta={"full_name": "Sarah", "email": "s@x.com", "phone": "0771234567"})
    description = _fired(notified, "user.signin")[0]["description"]
    assert "s@x.com" not in description and "0771234567" not in description
