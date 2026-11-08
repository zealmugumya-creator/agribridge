# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""One login, one type of account — the server answers, the database enforces.

The owner's complaint is specific: "they shouldn't create a farmer account and then
create a buyer account", and the person on WhatsApp who wrote "the old account is not
accessible, I creating a new one" is what that looks like when it goes wrong. Two
things have to be true for that sentence to hold, and they are different layers:

  * migration 0028 makes a second profile row impossible *in the database*. Its
    guard is a SECURITY DEFINER trigger, measured in
    tests/sql/one_role_per_person.test.mjs to refuse the write for service_role too —
    which is why this backend cannot pre-check its way around it and must translate
    the SQLSTATE it raises.
  * this file decides what the app *believes* a login is, and that answer has to be
    server-written. user_metadata.role is set by the signed-in browser
    (setAccountRole in static/index.html, until this stage), and the profile screen
    offered it as an always-editable dropdown — the very switch the owner asked to close.

Pinned below: the guard's refusal reaching a person as a sentence they can act on
instead of as "Could not save profile"; the same refusal on both admin write paths;
the resolution order (server choice, then the privilege row, then the guarded profile
table, then metadata, and nothing at all when the store cannot be read); and that
choosing an account type is a one-time act, including under two racing requests.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import RpcResult

import app as app_mod

REPO = Path(__file__).resolve().parent.parent
UID = "4f1e2b3c-5d6a-4b7c-8d9e-0f1a2b3c4d5e"
PHONE = "+256771234567"


class _Resp:
    def __init__(self, status=201, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        if not self._payload:
            raise ValueError("empty body")
        return self._payload


@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda *a, **k: (UID, None))


@pytest.fixture
def admin_header():
    return {"Authorization": f"Bearer {app_mod.make_token({'role': 'admin'})}"}


def _put_profile(client, body, ip="10.9.0.1"):
    return client.post("/api/user/profile", json=body,
                       headers={"Authorization": "Bearer tok"},
                       environ_overrides={"REMOTE_ADDR": ip})


# ── 1. the guard, translated ─────────────────────────────────────────────────
def _guard_post(seen, message="this person is already registered as a buyer"):
    """A PostgREST answer shaped like 0028's trigger firing on the caller's own write."""
    def _post(url, json=None, headers=None, timeout=None):
        seen.append((url, headers["Authorization"], json))
        return _Resp(400, {"code": "GP001", "message": message,
                           "details": None, "hint": None})
    return _post


@pytest.fixture
def refused_write(monkeypatch):
    """The caller's own profile write comes back as the one-role guard."""
    seen: list = []
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])
    monkeypatch.setattr(app_mod.requests, "post", _guard_post(seen))
    return seen


def test_a_second_profile_row_is_named_instead_of_collapsed(client, session, refused_write):
    """GP001 says which account already exists, and that word is the useful part."""
    r = _put_profile(client, {"full_name": "Sarah Auma", "phone": "0771 234 567"})
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "already_another_role"
    assert body["existing_role"] == "buyer"
    assert "buyer account" in body["error"]
    assert "recover" in body["error"]


def test_the_guard_is_not_retried_with_the_backend_key(client, session, refused_write):
    """The 42501 retry exists for a missing policy. A trigger answer is not that.

    Retrying GP001 with the backend key would be a second round trip to a guard that
    is SECURITY DEFINER, i.e. one that answers the same whichever key arrives, and
    every attempt holds one of Render's two sync workers for its whole duration.
    """
    _put_profile(client, {"full_name": "Sarah Auma", "phone": "0771 234 567"})
    assert len(refused_write) == 1, f"{len(refused_write)} writes for one guard refusal"
    assert refused_write[0][1] == "Bearer tok", "the caller's own token is what was refused"


def test_a_guard_refusal_sends_no_personal_data_back(client, session, refused_write):
    r = _put_profile(client, {"full_name": "Sarah Auma", "phone": "0771 234 567"})
    text = r.get_data(as_text=True)
    assert "256771234567" not in text and "Sarah" not in text
    assert "already registered" not in text, "the trigger sentence is translated, not echoed"


def test_an_unreadable_guard_message_still_refuses_in_plain_terms(client, session, monkeypatch):
    seen: list = []
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])

    def _post(url, json=None, headers=None, timeout=None):
        seen.append(url)
        return _Resp(400, {"code": "GP001", "message": ""})

    monkeypatch.setattr(app_mod.requests, "post", _post)
    r = _put_profile(client, {"full_name": "Sarah Auma", "phone": "0771 234 567"})
    assert r.status_code == 409 and seen == [f"{app_mod.SUPABASE_URL}/rest/v1/farmers"]
    assert r.get_json()["existing_role"] == ""
    assert "another type" in r.get_json()["error"]


# ── 2. the admin console meets the same wall ─────────────────────────────────
class _Inserting:
    """Stands in for SUPA so a write can be refused with a chosen SQLSTATE."""
    def __init__(self, result=None):
        self.calls = []
        self.result = result or RpcResult(ok=True, data=[{"id": "row-1"}])

    def insert(self, table, rows):
        self.calls.append((table, rows))
        return self.result


def _gp001(role):
    return RpcResult(ok=False, status=400, error=json.dumps(
        {"code": "GP001", "message": f"this person is already registered as a {role}"}))


def test_an_admin_opening_a_transporter_for_an_existing_farmer_is_told_which(
        client, admin_header, monkeypatch):
    db = _Inserting(_gp001("farmer"))
    monkeypatch.setattr(app_mod, "SUPA", db)
    r = client.post("/api/admin/transporters",
                    json={"full_name": "Peter Okello", "phone": "+256771000016",
                          "supabase_uid": UID},
                    headers=admin_header)
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "already_another_role" and body["existing_role"] == "farmer"
    assert "farmer" in body["error"]
    assert db.calls == [("transporters", {
        "full_name": "Peter Okello", "phone": "+256771000016", "supabase_uid": UID,
        "is_available": False, "status": "active"})], "the row is sent as before, unchanged"


def test_a_transporter_created_without_a_clash_still_returns_the_row(
        client, admin_header, monkeypatch):
    db = _Inserting(RpcResult(ok=True, data=[{"id": "t-1", "full_name": "Peter Okello"}]))
    monkeypatch.setattr(app_mod, "SUPA", db)
    r = client.post("/api/admin/transporters",
                    json={"full_name": "Peter Okello", "phone": "+256771000016"},
                    headers=admin_header)
    assert r.status_code == 201
    assert r.get_json()["transporter"] == {"id": "t-1", "full_name": "Peter Okello"}


def test_a_failed_insert_without_the_guard_code_is_still_a_plain_failure(
        client, admin_header, monkeypatch):
    db = _Inserting(RpcResult(ok=False, status=500,
                              error=json.dumps({"code": "XX000", "message": "boom"})))
    monkeypatch.setattr(app_mod, "SUPA", db)
    r = client.post("/api/admin/transporters",
                    json={"full_name": "Peter Okello", "phone": "+256771000016"},
                    headers=admin_header)
    assert r.status_code == 500
    assert r.get_json() == {"error": "Failed to create transporter"}


def test_the_editor_on_a_role_table_reports_the_guard(client, admin_header, monkeypatch):
    """/api/admin/row can insert farmers and transporters, so it needs the same words."""
    db = _Inserting(_gp001("transporter"))
    monkeypatch.setattr(app_mod, "SUPA", db)
    r = client.post("/api/admin/row",
                    json={"table": "farmers", "fields": {"full_name": "Sarah Auma",
                                                          "phone": PHONE}},
                    headers=admin_header)
    assert r.status_code == 409
    assert r.get_json()["code"] == "already_another_role"
    assert r.get_json()["existing_role"] == "transporter"


def test_a_non_role_table_keeps_the_route_it_had(client, admin_header, monkeypatch):
    """listings is not guarded, so it must not be routed through the new helper."""
    called = []
    monkeypatch.setattr(app_mod, "supa_insert",
                        lambda *a, **k: called.append(a) or [{"id": "l-1"}])
    db = _Inserting()
    monkeypatch.setattr(app_mod, "SUPA", db)
    r = client.post("/api/admin/row",
                    json={"table": "listings", "fields": {"crop_name": "Maize",
                                                           "price_per_kg": 900}},
                    headers=admin_header)
    assert r.status_code == 200
    assert called == [("listings", {"crop_name": "Maize", "price_per_kg": 900})]
    assert db.calls == [], "an unguarded table costs one request, not two"


def test_only_the_three_guarded_tables_take_the_new_path():
    """0028 attaches to exactly these; a fourth would silently keep the old behaviour."""
    assert app_mod._ROLE_TABLES == frozenset({"farmers", "buyers", "transporters"})
    sql = (REPO / "supabase/migrations/0028_one_role_per_person.up.sql").read_text(
        encoding="utf-8")
    for table in app_mod._ROLE_TABLES:
        assert f"on public.{table}" in sql, f"0028 installs no trigger on {table}"


# ── 3. what the server believes a login is ───────────────────────────────────
class _Store:
    """user_roles reads plus the three profile tables, answered per test.

    `reads` scripts the user_roles reads in order: two of the rules below — a lost
    race and an unreadable store — only show themselves on the second read, and a
    fixture that could only answer once would have to fake them some other way.
    """
    def __init__(self, business=None, privilege=None, error="", reads=None, **profile):
        self.profile = profile
        self.error = error
        self.reads = list(reads) if reads is not None else None
        self.rows = [] if (business is None and privilege is None) else \
            [{"user_id": UID, "business_role": business, "role": privilege}]
        self.checked_calls = []

    def get_checked(self, table, filters=None, limit=100):
        assert table == "user_roles", f"the store was asked about {table}"
        self.checked_calls.append(filters)
        if self.reads is not None:
            return list(self.reads.pop(0) if self.reads else []), ""
        if self.error:
            return None, self.error
        return list(self.rows), ""

    def get(self, table, filters=None, limit=100):
        return list(self.profile.get(table, []))


@pytest.fixture
def store(monkeypatch):
    def _install(**kw):
        fake = _Store(**kw)
        monkeypatch.setattr(app_mod, "supa_get_checked", fake.get_checked)
        monkeypatch.setattr(app_mod, "supa_get", fake.get)
        return fake
    return _install


def test_the_server_written_choice_beats_everything_else(store):
    with app_mod.app.test_request_context("/api/x"):
        store(business="hotel", privilege="admin", farmers=[{"id": UID}])
        assert app_mod.resolve_account_type(UID, "farmer") == ("hotel", "business_role")


def test_an_exporter_holding_a_farmers_row_is_still_an_exporter(store):
    """The core of the exporter design: they sell through the same farmers row a
    farmer uses (listings.farmer_id points there), and that row on its own reads as
    'farmer'. The recorded choice is what keeps the answer 'exporter' — which is
    why the SPA writes the type BEFORE the row exists."""
    with app_mod.app.test_request_context("/api/x"):
        store(business="exporter", farmers=[{"id": UID}])
        assert app_mod.resolve_account_type(UID, "farmer") == ("exporter", "business_role")


def test_exporter_metadata_answers_only_while_the_server_has_nothing(store):
    with app_mod.app.test_request_context("/api/x"):
        store()
        assert app_mod.resolve_account_type(UID, "exporter") == ("exporter", "metadata")


def test_an_operator_keeps_the_answer_it_had_before_this_stage(store):
    """A privilege row with no business_role resolves exactly as the old _auth_role()
    did, so the terms an admin accepts do not move under this stage."""
    with app_mod.app.test_request_context("/api/x"):
        store(privilege="superadmin")
        assert app_mod.resolve_account_type(UID, "vendor") == ("superadmin", "privilege")


def test_the_profile_table_that_holds_the_row_is_the_answer(store):
    with app_mod.app.test_request_context("/api/x"):
        store(farmers=[{"id": UID}])
        assert app_mod.resolve_account_type(UID, "vendor") == ("farmer", "profile")
    with app_mod.app.test_request_context("/api/x"):
        store(transporters=[{"id": "t-1"}])
        assert app_mod.resolve_account_type(UID, "vendor") == ("transporter", "profile")


def test_a_farmers_row_outranks_a_buyers_row_for_the_four_who_hold_both(store):
    """The app puts a farmer in farmers and that is where their listings and orders
    live, so the screens they are given must match the table that carries their work."""
    with app_mod.app.test_request_context("/api/x"):
        store(farmers=[{"id": UID}], buyers=[{"buyer_type": "hotel"}])
        assert app_mod.resolve_account_type(UID, "vendor") == ("farmer", "profile")


def test_a_buyers_row_answers_from_its_own_type_column(store):
    """base-schema:28 stores individual|vendor|hotel|restaurant|exporter|ngo. The app
    has no exporter tab, so the mapping is the SPA's own (getCurrentRole)."""
    for bt, want in [("hotel", "hotel"), ("restaurant", "hotel"),
                     ("vendor", "vendor"), ("individual", "vendor"), ("ngo", "vendor")]:
        with app_mod.app.test_request_context("/api/x"):
            store(buyers=[{"buyer_type": bt}])
            assert app_mod.resolve_account_type(UID, "farmer") == (want, "profile"), bt


def test_metadata_is_last_and_a_missing_store_is_not_guessed_past(store):
    with app_mod.app.test_request_context("/api/x"):
        store()
        assert app_mod.resolve_account_type(UID, "hotel") == ("hotel", "metadata")
    with app_mod.app.test_request_context("/api/x"):
        # A separate request: the answer above is memoised for the whole request.
        store()
        assert app_mod.resolve_account_type(UID, "nurse") == ("", "")
    with app_mod.app.test_request_context("/api/x"):
        # The read failed, so a choice may already be sitting in that row. Answering
        # 'metadata' here would outrank it; answering nothing keeps the door honest.
        store(error="42501 permission denied")
        assert app_mod.resolve_account_type(UID, "farmer") == ("", "unreadable")


def test_the_answer_is_read_once_per_request(store):
    """_auth_role() is called several times inside one legal reply and each call used
    to cost a round trip; on a box with two sync workers that is not free."""
    fake = store(business="farmer")
    with app_mod.app.test_request_context("/api/x"):
        for _ in range(4):
            app_mod.resolve_account_type(UID, "vendor")
        assert len(fake.checked_calls) == 1


def test_an_operator_reading_the_route_is_not_offered_a_choice(client, session, store):
    store(privilege="admin")
    r = client.get("/api/user/account-type", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["role"] == "admin" and body["source"] == "privilege"
    assert body["can_choose"] is False and "choices" not in body


def test_a_person_with_nothing_to_go_on_is_offered_the_choice(client, session, store):
    store()
    r = client.get("/api/user/account-type", headers={"Authorization": "Bearer tok"})
    body = r.get_json()
    assert body["can_choose"] is True
    assert [c["value"] for c in body["choices"]] == ["farmer", "vendor", "hotel", "supplier",
                                                   "exporter"]


def test_a_farmer_is_told_what_they_are_and_not_asked_again(client, session, store):
    store(farmers=[{"id": UID}])
    r = client.get("/api/user/account-type", headers={"Authorization": "Bearer tok"})
    body = r.get_json()
    assert (body["role"], body["source"], body["label"]) == ("farmer", "profile", "Farmer")
    assert body["can_choose"] is False


def test_the_route_needs_a_session(client):
    assert client.get("/api/user/account-type").status_code == 401
    assert client.post("/api/user/account-type", json={"role": "farmer"}).status_code == 401


# ── 4. choosing is a one-time act ────────────────────────────────────────────
class _AccountStore:
    def __init__(self):
        self.update_calls = []
        self.insert_calls = []
        self.update_result = RpcResult(ok=True, data=[{"business_role": "farmer"}])
        self.insert_result = RpcResult(ok=True, data=[{"business_role": "farmer"}])

    def update_checked(self, table, data, filters):
        self.update_calls.append((table, data, filters))
        return self.update_result

    def insert(self, table, rows):
        self.insert_calls.append((table, rows))
        return self.insert_result


@pytest.fixture
def supa(monkeypatch):
    db = _AccountStore()
    monkeypatch.setattr(app_mod, "SUPA", db)
    return db


def _choose(client, role, ip="10.9.1.1"):
    return client.post("/api/user/account-type", json={"role": role},
                       headers={"Authorization": "Bearer tok"},
                       environ_overrides={"REMOTE_ADDR": ip})


def test_a_first_choice_is_written_to_the_store_nobody_else_can_edit(
        client, session, supa, store):
    store()
    r = _choose(client, "farmer")
    assert r.status_code == 200 and r.get_json()["role"] == "farmer"
    assert supa.update_calls == [("user_roles", {"business_role": "farmer"},
                                  {"user_id": f"eq.{UID}", "business_role": "is.null"})], \
        "the write is conditional on the column still being empty"
    assert supa.insert_calls == [], "an existing row is not re-inserted"


def test_a_second_choice_is_refused_and_nothing_is_written(client, session, supa, store):
    store(business="farmer")
    r = _choose(client, "hotel")
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "account_type_locked" and body["role"] == "farmer"
    assert "one type of account" in body["error"]
    assert supa.update_calls == [] and supa.insert_calls == []


def test_repeating_the_same_choice_is_not_an_error(client, session, supa, store):
    """A person who reloads and taps Save again is not breaking a rule."""
    store(business="farmer")
    r = _choose(client, "farmer")
    assert r.status_code == 200 and r.get_json()["note"] == "already set"
    assert supa.update_calls == []


def test_a_word_that_is_not_an_account_type_is_refused(client, session, supa, store):
    """'buyer' is the word the owner used, and it is still not a role: the app's five
    are the signup select, and a transporter is issued by an operator."""
    store()
    for i, bad in enumerate(["buyer", "admin", "transporter", "", "superadmin"]):
        r = _choose(client, bad, ip=f"10.9.2.{i + 1}")
        assert r.status_code == 400, bad
    assert supa.update_calls == []


def test_an_operator_login_is_not_turned_into_a_marketplace_account(
        client, session, supa, store):
    store(privilege="admin")
    r = _choose(client, "farmer")
    assert r.status_code == 409
    assert "operator account" in r.get_json()["error"]
    assert supa.update_calls == [] and supa.insert_calls == []


def test_a_login_with_no_row_yet_gets_one_without_a_role_column(client, session, supa, store):
    """Only operators were ever given a user_roles row. Creating one must not claim a
    privilege: 0002:30 defaults `role` to 'user', and `role` is not in the payload."""
    store()
    supa.update_result = RpcResult(ok=True, data=[])   # zero rows: no user_roles row
    r = _choose(client, "supplier")
    assert r.status_code == 200
    assert supa.insert_calls == [("user_roles", {"user_id": UID, "business_role": "supplier"})]
    assert "role" not in supa.insert_calls[0][1]


def test_a_race_lands_as_one_choice_not_two(client, session, supa, store):
    """The conditional update answers with zero rows when someone set the column in
    between, and theirs stands — this request does not overwrite it."""
    store(reads=[[{"user_id": UID, "business_role": None, "role": "user"}],
                 [{"user_id": UID, "business_role": "hotel", "role": "user"}]])
    supa.update_result = RpcResult(ok=True, data=[])
    r = _choose(client, "vendor")
    assert r.status_code == 200
    assert r.get_json()["role"] == "hotel", "the first choice is the one that counts"
    assert supa.insert_calls == []


def test_a_store_that_cannot_be_read_does_not_open_the_choice(client, session, supa, store):
    store(error="connection refused")
    r = _choose(client, "farmer")
    assert r.status_code == 503
    assert supa.update_calls == [] and supa.insert_calls == []


def test_a_refused_write_says_retry_and_leaves_nothing_half_done(client, session, supa, store):
    store()
    supa.update_result = RpcResult(ok=False, status=403, error="permission denied")
    r = _choose(client, "farmer")
    assert r.status_code == 503
    assert "retry" in r.get_json()["error"]
    assert supa.insert_calls == []


def test_a_login_already_held_by_a_profile_table_cannot_choose_past_it(
        client, session, supa, store):
    """business_role is not the only evidence of what a login is.

    The GET answers can_choose:false for a login that appears in farmers, but the
    browser is not the only caller of the POST — and an empty business_role next to a
    real farmer's row is precisely the shape Stage N5 leaves behind when a profile is
    saved without a choice being made. Recording 'vendor' there would let a column
    nobody wrote outrank the table that carries this person's listings, which is the
    switch the owner asked closed: "they shouldn't create a farmer account and then
    create a buyer account".
    """
    store(farmers=[{"id": UID}])
    r = _choose(client, "vendor")
    assert r.status_code == 409
    body = r.get_json()
    assert body["code"] == "account_type_locked"
    assert (body["role"], body["source"]) == ("farmer", "profile")
    assert "Farmer account" in body["error"] and "Vendor / buyer" in body["error"]
    assert supa.update_calls == [] and supa.insert_calls == []


def test_a_driver_is_not_offered_a_way_to_become_a_buyer(client, session, supa, store):
    """A transporter row is created by an operator, and transporter is not one of the
    four words a person may choose — so whichever word arrives, the answer is theirs."""
    store(transporters=[{"id": "t-1"}])
    r = _choose(client, "hotel")
    assert r.status_code == 409
    assert r.get_json()["role"] == "transporter"
    assert supa.update_calls == [] and supa.insert_calls == []


def test_a_choice_that_matches_the_table_holding_them_is_recorded(client, session,
                                                                  supa, store):
    """Agreeing with the profile row is not a role switch, and writing it down is the
    point: after this the answer stops depending on which table the read found."""
    store(farmers=[{"id": UID}])
    r = _choose(client, "farmer")
    assert r.status_code == 200 and r.get_json()["role"] == "farmer"
    assert supa.update_calls == [("user_roles", {"business_role": "farmer"},
                                  {"user_id": f"eq.{UID}", "business_role": "is.null"})]


def test_the_get_and_the_post_agree_on_who_is_locked(client, session, supa, store):
    """can_choose:false and a 409 are the same statement in two shapes. If they ever
    disagree, a person is offered a dropdown the server will refuse — or told to go to
    the server for a choice the panel already made for them."""
    for table, row, role in [("farmers", {"id": UID}, "farmer"),
                             ("transporters", {"id": "t-1"}, "transporter"),
                             ("buyers", {"buyer_type": "hotel"}, "hotel")]:
        store(**{table: [row]})
        got = client.get("/api/user/account-type", headers={"Authorization": "Bearer tok"})
        assert got.status_code == 200
        body = got.get_json()
        assert body["role"] == role and body["source"] == "profile", table
        assert body["can_choose"] is False, table
        refused = _choose(client, "supplier", ip=f"10.9.3.{len(table)}")
        assert refused.status_code == 409, table
        assert refused.get_json()["role"] == role, table
    assert supa.update_calls == [] and supa.insert_calls == []


# ── 4b. the exporter type (Stage E1) ─────────────────────────────────────────
def test_choosing_exporter_is_written_like_any_other_choice(client, session, supa, store):
    store()
    r = _choose(client, "exporter", ip="10.9.4.1")
    assert r.status_code == 200 and r.get_json()["role"] == "exporter"
    assert supa.update_calls == [("user_roles", {"business_role": "exporter"},
                                  {"user_id": f"eq.{UID}", "business_role": "is.null"})]


def test_the_choice_is_offered_with_the_label_the_signup_select_uses(client, session, store):
    store()
    r = client.get("/api/user/account-type", headers={"Authorization": "Bearer tok"})
    choices = {c["value"]: c["label"] for c in r.get_json()["choices"]}
    assert choices["exporter"] == "Exporter / trading company"


def test_a_farmers_row_closes_the_door_on_exporter_too(client, session, supa, store):
    """The same lock that stops a farmer becoming a hotel stops one becoming an
    exporter after the fact: the choice has to be made before the row exists.
    This is the rule the SPA's saveProfileWithRetry gate is built around."""
    store(farmers=[{"id": UID}])
    r = _choose(client, "exporter", ip="10.9.4.2")
    assert r.status_code == 409
    assert r.get_json()["role"] == "farmer"
    assert supa.update_calls == [] and supa.insert_calls == []


# ── 5. the rest of the app reads the same answer ─────────────────────────────
def test_the_terms_a_person_sees_follow_the_server_not_their_own_field(session, store):
    """_auth_role() used to hand back user_metadata.role, which the browser writes
    itself. A farmer who set that field to 'vendor' was shown vendor terms."""
    store(business="farmer")
    with app_mod.app.test_request_context("/api/x", headers={"Authorization": "Bearer tok"}):
        app_mod.g.auth_user = {"id": UID, "user_metadata": {"role": "vendor"}}
        assert app_mod._auth_role() == "farmer"


def test_the_privilege_row_still_answers_for_legal_uses(session, store):
    with app_mod.app.test_request_context("/api/x"):
        app_mod.g.auth_user = {"id": UID, "user_metadata": {"role": "farmer"}}
        store(privilege="admin")
        assert app_mod._auth_role() == "admin"


def test_nothing_is_invented_when_the_store_answers_nothing(session, store):
    with app_mod.app.test_request_context("/api/x"):
        app_mod.g.auth_user = {"id": UID, "user_metadata": {}}
        store()
        assert app_mod._auth_role() is None


def test_an_unreadable_store_leaves_the_old_answer_for_terms(session, store):
    """The choice route refuses when user_roles cannot be read, and _auth_role() does
    not: a missing grant or an un-applied 0028 must not make a farmer accept a
    buyer's terms. That fallback is what this route did before Stage N9.
    """
    with app_mod.app.test_request_context("/api/x"):
        app_mod.g.auth_user = {"id": UID, "user_metadata": {"role": "farmer"}}
        store(error="42P01 relation public.user_roles does not exist")
        assert app_mod._auth_role() == "farmer"


def test_a_profile_row_still_answers_when_the_store_is_unreadable(session, store):
    """Only the *choice* is withheld: a farmers row is evidence the read cannot touch."""
    with app_mod.app.test_request_context("/api/x"):
        app_mod.g.auth_user = {"id": UID, "user_metadata": {"role": "vendor"}}
        store(error="connection refused", farmers=[{"id": UID}])
        assert app_mod._auth_role() == "farmer"


def test_a_caller_with_no_session_has_no_role():
    with app_mod.app.test_request_context("/api/x"):
        app_mod.g.auth_user = {}
        assert app_mod._auth_role() is None


# ── 6. the panel on the other side of these two routes ───────────────────────
@pytest.fixture(scope="module")
def spa():
    return (REPO / "static" / "index.html").read_text(encoding="utf-8")


def _fn(spa: str, header: str) -> str:
    """The brace-balanced body that follows `header`, read from the shipped file.

    Slice-to-next-`}` helpers keep tripping over this SPA's nested blocks, and the
    rule being pinned here is about which branch of an if returns what.
    """
    start = spa.index(header)
    open_at = spa.index("{", start)
    depth = 0
    for i in range(open_at, len(spa)):
        if spa[i] == "{":
            depth += 1
        elif spa[i] == "}":
            depth -= 1
            if depth == 0:
                return spa[open_at:i + 1]
    raise AssertionError(f"unbalanced body after {header!r}")


def test_the_account_panel_shows_a_choice_only_while_one_is_open(spa):
    """'there shouldn't create a farmer account and then create a buyer account' is a
    UI statement as much as a database one: a dropdown that is always editable is the
    thing the owner read as permission to switch.

    The select now exists only in the branch the server did *not* close.
    """
    picker = _fn(spa, "function _accountTypePicker(role)")
    assert "can_choose===false" in picker, "the panel no longer asks the server's answer"
    assert picker.index("can_choose===false") < picker.index('<select id="setRole"'), (
        "the dropdown must come after the locked branch, not before it"
    )
    assert "Set once and kept by AgriBridge" in picker
    assert "opening a second one" in picker, (
        "the locked line has to send the person to the operator, not to a new signup"
    )


def test_the_settings_account_screen_defers_to_that_one_helper(spa):
    panel = _fn(spa, "else if(screen==='account')")
    assert "_accountTypePicker(role)" in panel
    assert 'id="setRole"' not in panel, (
        "a second, always-editable copy of the control would reopen the switch"
    )


def test_the_panel_asks_the_server_when_it_has_nothing_to_show(spa):
    """A login predating this stage has no cached answer, so the panel would otherwise
    sit on the metadata guess forever. Asked once, and never re-asked on a failure:
    every Settings open costing a round trip on Render's two sync workers is a worse
    answer than showing the dropdown and letting the POST decide."""
    once = _fn(spa, "function _askAccountTypeOnce()")
    assert "_accountTypeAsked || _accountType" in once
    assert "loadAccountType()" in once
    assert "repaintAccountPanel()" in once
    assert "_askAccountTypeOnce();" in _fn(spa, "if(screen==='account'){\n    var n=")


def test_the_choice_is_written_on_the_server_and_never_into_the_profile(spa):
    body = _fn(spa, "async function setAccountRole(r)")
    assert "abApi('/api/user/account-type'" in body
    assert "user_metadata" not in body, (
        "the field this function used to write is the one the signed-in browser can "
        "rewrite at any time — that is the switch, not the store"
    )
    assert "sbAuth.auth.updateUser" not in body
    assert "can_choose:false" in body, "a made choice is not offered twice"
    assert "res.json.source" in body, (
        "the 409 says whether the answer comes from their choice, an operator row or "
        "a profile table; guessing 'business_role' would mislabel the panel"
    )


def test_the_cached_answer_is_dropped_with_the_session(spa):
    """ab_account_type is a per-person answer. Cached on a shared phone it would show
    the next signer-in the previous person's navigation."""
    logout = _fn(spa, "async function logOut()")
    auth = spa[spa.index("onAuthStateChange(function(event, session)"):]
    auth = auth[:auth.index("sbAuth.auth.getSession")]
    for body, where in [(logout, "logOut()"),
                        (auth, "the signed-out branch of onAuthStateChange")]:
        assert re.search(r"_accountType\s*=\s*null", body), where
        assert "localStorage.removeItem('ab_account_type')" in body, where
        assert re.search(r"_accountTypeAsked\s*=\s*false", body), (
            f"{where} clears the answer but not the 'already asked' flag, so the next "
            "login in the same tab is never offered the choice"
        )


def test_the_navigation_follows_the_server_answer_and_not_the_guess(spa):
    body = _fn(spa, "function getCurrentRole()")
    assert "_accountType" in body
    assert body.index("var server") < body.index("currentUser.role"), (
        "the server's answer has to be tried before the metadata value"
    )
    assert "'admin'" in body, (
        "an operator is a privilege, not a marketplace account, and must keep the "
        "view the console was built for"
    )


# ── 7. the exporter on the other side of the choice (Stage E1) ───────────────
def test_both_signup_selects_offer_the_exporter_type(spa):
    for select in ("regRole", "oauthRole"):
        tag = spa[spa.index(f'<select id="{select}"'):]
        tag = tag[:tag.index("</select>")]
        assert 'value="exporter"' in tag, select


def test_the_navigation_knows_the_exporter_word(spa):
    body = _fn(spa, "function getCurrentRole()")
    assert "if (role === 'exporter') return 'exporter';" in body


def test_an_exporter_navigates_the_selling_surfaces(spa):
    """They list produce and fulfil orders exactly as a farmer does — the five
    seller tabs, not the buyer's market browse."""
    nav = _fn(spa, "var ROLE_NAV =")
    farmer = re.search(r"farmer:\s*(\[.*?\])", nav).group(1)
    exporter = re.search(r"exporter:\s*(\[.*?\])", nav).group(1)
    assert farmer == exporter


def test_the_exporter_type_is_recorded_before_the_row_that_would_freeze_it(spa):
    """A farmers row on its own reads as 'farmer' and locks the choice (pinned in
    section 4b). All three onboarding paths — immediate signup, the deferred
    pending-profile flush, OAuth — pass through saveProfileWithRetry, so the type
    POST comes first there. A 409 means the server already holds a type and the
    profile write meets the same guard; anything else must defer the whole save
    rather than write a row that freezes this login as a farmer forever."""
    body = _fn(spa, "async function saveProfileWithRetry(profile, attempts)")
    gate = "if ((profile.role || '') === 'exporter')"
    assert gate in body
    assert body.index(gate) < body.index("'/api/user/profile'"), (
        "the type must be recorded before the farmers row exists")
    assert "ra.status !== 409" in body
    assert "throw new Error('account type not recorded yet')" in body


def test_the_oauth_flow_queues_the_same_pending_profile_as_email_signup(spa):
    """Email confirmation can defer the profile save by minutes or days; without
    the queue, an OAuth exporter who closes the tab mid-onboarding loses the save
    and the role with it."""
    body = _fn(spa, "async function saveOAuthProfile()")
    assert "localStorage.setItem('ab_pending_profile'" in body
    assert body.index("ab_pending_profile") < body.index("saveProfileWithRetry(")
