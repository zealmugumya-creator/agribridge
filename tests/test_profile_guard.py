# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Editing your own profile is a validated write, and its answer is honest.

Migration 0008 already refuses a one-character name, a phone that is not +256
format, and a second account on one number — but until now the profile editor
threw that answer away: it PATCHed Supabase directly, ignored the result, and
toast-confirmed "Profile saved ✓" whatever happened. A person whose number had
just been claimed was told it worked. These tests pin the route the editor now
goes through, the codes it maps, and the fact that a refusal reaches the person
as words instead of as a green tick.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import app as app_mod

REPO = Path(__file__).resolve().parent.parent
UID = "4f1e2b3c-5d6a-4b7c-8d9e-0f1a2b3c4d5e"


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
    """The caller holds a verified Supabase session for that farmer row."""
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda *a, **k: (UID, None))


def _put(client, body, ip="10.7.0.1", token=True):
    headers = {"Authorization": "Bearer tok"} if token else {}
    return client.post("/api/user/profile", json=body, headers=headers,
                       environ_overrides={"REMOTE_ADDR": ip})


@pytest.fixture
def saved(monkeypatch):
    """The write is captured, not performed. `saved["post"]` is what reached the table."""
    taken: dict = {}
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])
    monkeypatch.setattr(app_mod.requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        taken.update(post=json) or _Resp(201))
    return taken


@pytest.fixture
def visits(monkeypatch):
    """Counts both halves of the route: the read that looks for a clash, and the write."""
    seen: list = []
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: seen.append(("read", a)) or [])
    monkeypatch.setattr(app_mod.requests, "post",
                        lambda *a, **k: seen.append(("write", a)) or _Resp(201))
    return seen


def _writes(seen):
    return [v for v in seen if v[0] == "write"]


# ── the write itself ─────────────────────────────────────────────────────────
def test_a_valid_edit_is_written_in_the_form_the_database_accepts(client, session, saved):
    r = _put(client, {"full_name": "Sarah Auma", "phone": "0771 234 567", "district": "Kasese"},
             ip="10.7.1.1")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert saved["post"] == {"id": UID, "full_name": "Sarah Auma",
                             "phone": "+256771234567", "district": "Kasese"}


def test_the_row_is_keyed_to_the_verified_session_not_to_the_body(client, session, saved):
    # A body naming another id must not move that other person's profile.
    _put(client, {"id": "someone-else", "user_id": "someone-else", "full_name": "Taken Over"},
         ip="10.7.1.2")
    assert saved["post"]["id"] == UID
    assert "someone-else" not in str(saved["post"])


def test_a_field_left_blank_means_unchanged(client, session, saved):
    r = _put(client, {"district": "Ibanda"}, ip="10.7.1.3")
    assert r.status_code == 200
    assert saved["post"] == {"id": UID, "district": "Ibanda"}


def test_a_name_of_only_spaces_is_not_saved_as_a_blank_name(client, session, visits):
    r = _put(client, {"full_name": "   "}, ip="10.7.1.4")
    assert r.status_code == 200
    assert _writes(visits) == [], "'nothing given' is 'nothing to write', not 'clear the column'"


def test_anonymous_and_missing_fields_are_never_a_failure(client, session, saved):
    assert _put(client, {}, ip="10.7.1.5").status_code == 200


def test_the_route_needs_a_real_session(client):
    r = _put(client, {"full_name": "Sarah Auma"}, ip="10.7.1.6", token=False)
    assert r.status_code == 401


# ── the identity rules, enforced before a row is written ────────────────────
@pytest.mark.parametrize("name", ["S", "  A  ", "test", "TEST", "Admin", "farmer", "null",
                                  "n/a", "unknown user", "Unknown", " Unknown User "])
def test_a_name_that_is_not_a_name_is_refused(client, session, visits, name):
    r = _put(client, {"full_name": name}, ip=f"10.7.2.{abs(hash(name)) % 200}")
    assert r.status_code == 400, name
    assert "real full name" in r.get_json()["error"] or "at least 2" in r.get_json()["error"]
    assert _writes(visits) == []


def _balanced(text: str, marker: str, open_ch: str, close_ch: str) -> str:
    i = text.index(marker) + len(marker)
    start = text.index(open_ch, i)
    depth = 0
    for j in range(start, len(text)):
        if text[j] == open_ch:
            depth += 1
        elif text[j] == close_ch:
            depth -= 1
            if depth == 0:
                return text[start + 1:j]
    raise AssertionError(f"unbalanced {open_ch}{close_ch} after {marker!r}")


def test_the_flask_blacklist_and_the_database_blacklist_are_the_same_list():
    """0008 kept its refusal list in Python while the database kept its own, and
    nothing noticed when 'Unknown User' walked through the gap between them. This
    test is the notice: migration 0025's farmers_full_name_real list and the
    /api/user/profile check must contain exactly the same names."""
    py = (REPO / "app.py").read_text(encoding="utf-8")
    route = py[py.index("def update_user_profile"):]
    route = route[:route.index("# Validate and normalise phone")]
    names_py = {t.lower() for t in re.findall(r"'([^']+)'", _balanced(route, " in ", "{", "}"))}

    sql = (REPO / "supabase/migrations/0025_identity_checks_all_users.up.sql")\
        .read_text(encoding="utf-8")
    # The list is built as SQL string literals, so each name is doubled-quoted.
    body = _balanced(sql, "lower(trim(full_name)) not in", "(", ")")
    names_sql = {t.lower() for t in re.findall(r"''([^']+)''", body)}

    assert names_py, "the profile route no longer has a readable refusal list"
    assert names_sql == {"test", "user", "admin", "farmer", "buyer", "name", "null",
                         "undefined", "none", "n/a", "unknown user", "unknown"}
    assert names_py == names_sql, (f"flask refuses {sorted(names_py - names_sql)}, "
                                   f"database refuses {sorted(names_sql - names_py)}")


@pytest.mark.parametrize("phone", ["0771", "07712345678901", "abc", "+10000000000",
                                   "12345", "+256"])
def test_a_number_that_is_not_a_phone_is_refused(client, session, visits, phone):
    """0030 widened the rule from Uganda to E.164, not to 'anything with digits'.
    '+10000000000' is the placeholder the backend used to stamp on nameless
    accounts: it passes the database's shape rule (it looks like a real NANP
    number), so its refusal lives here, in normalize_phone — the path every
    signup and profile save takes."""
    r = _put(client, {"phone": phone}, ip=f"10.7.3.{abs(hash(phone)) % 200}")
    assert r.status_code == 400, phone
    assert "country code" in r.get_json()["error"]
    assert _writes(visits) == []


@pytest.mark.parametrize("given,stored", [
    ("0771 234 567", "+256771234567"),
    ("+256771234567", "+256771234567"),
    ("256771234567", "+256771234567"),
    ("771234567", "+256771234567"),
    ("+254700123456", "+254700123456"),
    ("+20 103 679 7717", "+201036797717"),
    ("00201036797717", "+201036797717"),
])
def test_a_real_number_with_a_country_code_is_stored_in_one_shape(client, session, saved,
                                                                  given, stored):
    """The Egyptian exporter's number (+20 …) and the Kenyan buyer's (+254 …) must
    normalise to the same +digits shape the uniqueness index compares — a duplicate
    account on one number is only catchable if both signups store it identically."""
    r = _put(client, {"phone": given}, ip=f"10.7.6.{abs(hash(given)) % 200}")
    assert r.status_code == 200, given
    assert saved["post"]["phone"] == stored


def test_a_number_another_account_holds_is_refused_before_the_write(client, session,
                                                                     monkeypatch, visits):
    # The clash lookup answers with a row that is not ours.
    monkeypatch.setattr(app_mod, "supa_get",
                        lambda *a, **k: visits.append(("read", a)) or [{"id": "someone-else"}])
    r = _put(client, {"phone": "+256700111222"}, ip="10.7.4.1")
    assert r.status_code == 409
    assert "already registered" in r.get_json()["error"]
    assert _writes(visits) == []


# ── the database's own answer, passed through in words ───────────────────────
def _refuse(monkeypatch, code, status):
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])

    def _post(url, json=None, headers=None, timeout=None):
        # PostgREST echoes the offending value back — which is exactly why the
        # route must translate it instead of passing the body through.
        return _Resp(status, {
            "code": code,
            "message": 'new row for table "farmers" violates check constraint',
            "details": "Key (phone)=(" + str(json.get("phone")) + ") already exists.",
            "hint": None,
        })

    monkeypatch.setattr(app_mod.requests, "post", _post)


def test_a_lost_uniqueness_race_says_which_number_was_taken(client, session, monkeypatch):
    _refuse(monkeypatch, "23505", 409)
    r = _put(client, {"full_name": "Sarah Auma", "phone": "0700 111 222"}, ip="10.7.5.1")
    assert r.status_code == 409
    assert "already registered" in r.get_json()["error"]


def test_a_check_constraint_is_the_persons_to_fix_not_the_servers(client, session, monkeypatch):
    _refuse(monkeypatch, "23514", 400)
    r = _put(client, {"full_name": "Sarah", "phone": "+256771234567"}, ip="10.7.5.2")
    assert r.status_code == 400
    assert "real full name" in r.get_json()["error"]


def test_any_other_refusal_is_a_bad_gateway_not_a_lie(client, session, monkeypatch):
    _refuse(monkeypatch, "42P01", 500)
    r = _put(client, {"full_name": "Sarah Auma", "phone": "+256771234567"}, ip="10.7.5.3")
    assert r.status_code == 502
    assert r.get_json() == {"error": "Could not save profile"}


def test_a_policy_refusal_is_reported_as_the_machines_fault_not_the_persons(client, session,
                                                                            monkeypatch):
    """42501 is what PostgREST returns when the caller's own role may not write.

    Since the profile route now sends the caller's token first, this is the shape
    of "the database has not been brought up to migration 0026 yet" — the owner
    applies migrations by hand and 0018-0024 are still outstanding, so a new
    account's first save has no INSERT policy to allow it. The route retries once
    with the backend key rather than taking signup down with it, and only if that
    is refused too does the person hear about it. A message telling them to retype
    their name would send them to register a second account, which is the failure
    this whole stage exists to stop.
    """
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])
    seen: list = []

    def _post(url, json=None, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        return _Resp(403, {"code": "42501", "message": "permission denied for table farmers",
                           "details": None, "hint": None})

    monkeypatch.setattr(app_mod.requests, "post", _post)
    r = _put(client, {"full_name": "Sarah Auma", "phone": "+256771234567"}, ip="10.7.5.5")
    assert r.status_code == 503
    assert "not accepting profile saves" in r.get_json()["error"]
    assert seen[0] == "Bearer tok", "the caller's own token is tried first"
    assert len(seen) == 2 and seen[1] != seen[0], (
        "the retry is the one place the backend key may be used for this write")
    assert app_mod.SUPABASE_KEY in seen[1]


def test_a_successful_save_is_one_request_and_carries_no_backend_key(client, session,
                                                                     monkeypatch):
    """The backend-key retry runs only when the caller's own write is refused."""
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])
    seen: list = []
    monkeypatch.setattr(app_mod.requests, "post",
                        lambda url, json=None, headers=None, timeout=None:
                        seen.append(headers["Authorization"]) or _Resp(201))
    r = _put(client, {"full_name": "Sarah Auma", "phone": "+256771234567"}, ip="10.7.5.6")
    assert r.status_code == 200
    assert seen == ["Bearer tok"], (
        f"{len(seen)} attempt(s) with {[s[:12] for s in seen]}; a working policy must "
        "be exercised as the caller and must never need the backend key")


def test_the_database_error_body_never_reaches_the_person(client, session, monkeypatch):
    # The PostgREST body repeats the phone number and the colliding key.
    _refuse(monkeypatch, "23505", 409)
    r = _put(client, {"full_name": "Sarah Auma", "phone": "0700 111 222"}, ip="10.7.5.4")
    text = r.get_data(as_text=True)
    assert "256700111222" not in text and "already exists" not in text


def test_a_network_failure_says_so_without_a_status_code_leak(client, session, monkeypatch):
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **k: [])

    def _boom(*a, **k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(app_mod.requests, "post", _boom)
    r = _put(client, {"full_name": "Sarah Auma"}, ip="10.7.5.5")
    assert r.status_code == 502 and r.get_json()["error"] == "Could not save profile"


# ── the editor on the other side of this route ───────────────────────────────
@pytest.fixture(scope="module")
def spa():
    return (REPO / "static" / "index.html").read_text(encoding="utf-8")


def _profile_editor(spa):
    start = spa.index("async function saveProfile()")
    return spa[start:spa.index("\n}", start) + 2]


def test_the_profile_editor_asks_the_backend_and_believes_the_answer(spa):
    body = _profile_editor(spa)
    assert "abApi('/api/user/profile'" in body, (
        "profile edits must pass the same rules that guard sign-up")
    assert "sbUpdate('farmers'" not in body, (
        "a PATCH straight to Supabase is the write that no code ever checked")
    tick = "showToast('Profile saved ✓')"
    assert "if(!res.ok)" in body and tick in body
    assert body.index("if(!res.ok)") < body.index(tick), (
        "the green tick belongs after the write, not before it")


def test_the_profile_editor_does_not_remember_a_refused_edit(spa):
    body = _profile_editor(spa)
    refusal = body[body.index("if(!res.ok)"):body.index("commit();")]
    assert "return;" in refusal, "a refused edit must be abandoned, not kept"
    assert body.count("currentUser.name=next.name") == 1, (
        "local state moves in exactly one place: once the write succeeded")


def test_a_refused_save_is_said_in_words_the_person_can_act_on(spa):
    body = _profile_editor(spa)
    assert "res.json&&res.json.error" in body, (
        "the route's own sentence — 'already registered to another account' — is the useful one")
    assert "'Your session has ended. Sign in again, then save your profile.'" in body, (
        "the 401 body is the word 'Missing token', which no farmer can act on")
    assert "'We could not reach AgriBridge. Check your connection and try again.'" in body, (
        "abApi stands in for a failed request with the word 'network'")


def test_the_profile_editor_still_writes_nothing_it_was_not_given(spa):
    body = _profile_editor(spa)
    assert "nm || currentUser.name" in body and "ph || currentUser.phone" in body, (
        "an empty box means leave it, which is what the route already does")


def test_one_tap_on_save_is_one_write(spa):
    # Waiting for the answer made the gap long enough for a second tap, and each
    # POST occupies one of Render's two sync workers for the whole round trip.
    body = _profile_editor(spa)
    assert "if(!currentUser||_savingProfile) return;" in body
    assert body.index("_savingProfile=true") < body.index("await abApi('/api/user/profile'")
    assert "} finally { _savingProfile=false; }" in body, (
        "a refused save must not leave the button locked forever")


# ── the same phone rule on the signup side ───────────────────────────────────
def test_the_browser_normalises_to_the_same_shape_as_the_route(spa):
    """Signup used to accept any 9-12 digit number while the route refused
    everything that was not +256. Migration 0030 widens both to E.164, and the
    SPA helper must mirror normalize_phone in app.py — including the placeholder
    refusal the database's shape rule cannot express."""
    body = _balanced(spa, "function normalizePhoneE164(raw)", "{", "}")
    for fragment in ("s.indexOf('00') === 0", r"/^256[37]\d{8}$/", r"/^0[37]\d{8}$/",
                     r"/^[37]\d{8}$/", "d === '10000000000'", r"/^[1-9]\d{6,14}$/"):
        assert fragment in body, fragment


@pytest.mark.parametrize("header", ["async function doRegister()",
                                    "async function saveOAuthProfile()"])
def test_both_signup_flows_normalise_before_anything_leaves_the_browser(spa, header):
    body = _balanced(spa, header, "{", "}")
    assert "normalizePhoneE164(" in body, header
    assert body.index("normalizePhoneE164(") < body.index("PHONE_HINT"), (
        "the hint is shown when normalisation fails, not before it is tried")
    assert "phone = _norm;" in body or "phone = normalized;" in body, (
        "the stored value must be the normalised one, or the uniqueness index "
        "compares two shapes of the same number")
