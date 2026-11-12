# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Legal documents, acceptance, marketing consent and privacy requests.

Service logic runs against an in-memory fake of the database; the SQL guarantees
(append-only, RLS, immutability) are proven separately against real PostgreSQL in
tests/sql/legal_consent.test.mjs.
"""
from __future__ import annotations

import copy
import datetime as dt

import pytest
from conftest import FakeSupabase, RpcResult

import app as app_mod
from services import business
from services.legal import LegalService, normalize_role


class LegalDb(FakeSupabase):
    """Stateful fake for the tables the legal service touches."""
    def __init__(self):
        super().__init__()
        self.acceptances: list[dict] = []
        self.marketing: list[dict] = []
        self.requests: list[dict] = []
        self.fail_accept = False
        self.fail_insert = False

    def rpc(self, fn, args):
        self.rpc_calls.append((fn, args))
        if fn != "record_legal_acceptance":
            return RpcResult(ok=True)
        if self.fail_accept:
            return RpcResult(ok=False, status=503, error="db down")
        key = (args["p_user_id"], args["p_doc_type"], args["p_version"])
        if any((a["user_id"], a["doc_type"], a["version"]) == key for a in self.acceptances):
            return RpcResult(ok=True, data={"recorded": False, "already_accepted": True})
        self.acceptances.append({"user_id": args["p_user_id"], "doc_type": args["p_doc_type"],
                                 "version": args["p_version"], "hash": args["p_content_sha256"],
                                 "role": args["p_account_role"], "method": args["p_method"],
                                 "evidence": args["p_evidence"]})
        return RpcResult(ok=True, data={"recorded": True})

    def select(self, table, filters=None, limit=100):
        self.select_calls.append((table, filters))
        uid = (filters or {}).get("user_id", "").removeprefix("eq.")
        if table == "legal_acceptances":
            return [{"doc_type": a["doc_type"], "version": a["version"], "accepted_at": "2026-10-09T00:00:00Z",
                     "method": a["method"], "account_role": a["role"]}
                    for a in self.acceptances if a["user_id"] == uid]
        if table == "marketing_consent_events":
            return [{"id": i} for i, m in enumerate(self.marketing) if m["user_id"] == uid][:limit]
        if table == "marketing_consent_current":
            latest = {}
            for m in self.marketing:
                if m["user_id"] == uid:
                    latest[m["channel"]] = m
            return [{"channel": c, "granted": m["granted"], "recorded_at": "x"} for c, m in latest.items()]
        return []

    def insert(self, table, rows):
        self.insert_calls.append((table, rows))
        if self.fail_insert:
            return RpcResult(ok=False, status=500, error="boom")
        rows = rows if isinstance(rows, list) else [rows]
        if table == "marketing_consent_events":
            self.marketing.extend(copy.deepcopy(rows))
        if table == "privacy_requests":
            self.requests.extend(copy.deepcopy(rows))
            return RpcResult(ok=True, data=[{"id": "req-1"}])
        return RpcResult(ok=True, data=rows)


@pytest.fixture
def db():
    return LegalDb()


@pytest.fixture
def svc(db):
    return LegalService(db)


def _docs(svc, role):
    return [{"doc_type": d["doc_type"], "version": d["version"]} for d in svc.required_documents(role)]


# ── which documents each role sees ───────────────────────────────────────────
# Platform-wide policies every account type must accept (cookie is acknowledge-only).
PLATFORM_POLICIES = {"cookie-policy", "payment-refund-policy",
                     "delivery-dispute-policy", "acceptable-use-policy"}


@pytest.mark.parametrize("role,extra", [
    ("farmer", "farmer-terms"), ("vendor", "buyer-terms"), ("hotel", "buyer-terms"),
    ("supplier", "supplier-terms"), ("exporter", "exporter-terms"),
    ("transporter", "transporter-terms"),
])
def test_each_role_sees_only_its_own_terms(svc, role, extra):
    types = {d["doc_type"] for d in svc.required_documents(role)}
    assert types == {"terms-of-use", "privacy-policy", extra} | PLATFORM_POLICIES


def test_platform_policies_apply_to_every_role_and_cookie_is_acknowledge(svc):
    for role in ("farmer", "vendor", "hotel", "supplier", "exporter", "transporter"):
        by = {d["doc_type"]: d for d in svc.required_documents(role)}
        assert PLATFORM_POLICIES <= set(by), role
    cookie = next(d for d in svc.required_documents("farmer") if d["doc_type"] == "cookie-policy")
    assert cookie["requires_acceptance"] == "acknowledge"
    for k in ("payment-refund-policy", "delivery-dispute-policy", "acceptable-use-policy"):
        d = next(x for x in svc.required_documents("farmer") if x["doc_type"] == k)
        assert d["requires_acceptance"] == "accept", k


def test_unknown_or_missing_role_defaults_to_buyer_terms(svc):
    for raw in (None, "", "banana", "admin", "superadmin"):
        types = {d["doc_type"] for d in svc.required_documents(raw)}
        assert "buyer-terms" in types and "farmer-terms" not in types


def test_role_aliases_match_the_web_app():
    assert normalize_role("restaurant") == "hotel" and normalize_role("b2b") == "hotel"
    assert normalize_role("input_seller") == "supplier"
    assert normalize_role("exporter") == "exporter", (
        "an exporter must not fall through to buyer-terms — they sell, and their "
        "obligations (stock, packing, shipment, certification) are their own")
    assert normalize_role("transporter") == "transporter" and normalize_role("driver") == "transporter", (
        "a driver must not fall through to buyer-terms — getCurrentRole() maps both "
        "aliases to transporter, and their obligations are the Transporter Agreement's")


def test_no_public_admin_terms(svc):
    types = {d["doc_type"] for r in ("farmer", "vendor", "hotel", "supplier", "exporter", "transporter")
             for d in svc.required_documents(r)}
    assert not any("admin" in t or "staff" in t for t in types)


# ── status, acceptance, versions ─────────────────────────────────────────────
def test_new_user_has_everything_outstanding(svc):
    st = svc.status("u1", "farmer").data
    assert not st["complete"] and len(st["outstanding"]) == len(svc.required_documents("farmer"))


def test_acceptance_records_version_hash_role_and_method(svc, db):
    res = svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"),
                     method="login_review", evidence={"surface": "review_screen"})
    assert res.ok and len(res.data["recorded"]) == len(svc.required_documents("farmer"))
    by = {a["doc_type"]: a for a in db.acceptances}
    for d in svc.required_documents("farmer"):
        rec = by[d["doc_type"]]
        assert rec["version"] == d["version"] and rec["hash"] == d["sha256"]
        assert rec["role"] == "farmer" and rec["method"] == "login_review"
        assert rec["evidence"]["document_url"] == d["versioned_url"]
    assert svc.status("u1", "farmer").data["complete"]


def test_existing_user_is_not_asked_again_for_unchanged_terms(svc):
    svc.accept(user_id="u1", role="vendor", documents=_docs(svc, "vendor"), method="login_review")
    for _ in range(3):
        assert svc.status("u1", "vendor").data["outstanding"] == []


def test_publishes_versions_before_recording(svc, db):
    svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    assert db.upsert_calls and db.upsert_calls[0][0] == "legal_document_versions"


def test_stale_version_is_refused_with_409(svc):
    docs = _docs(svc, "farmer")
    docs[0]["version"] = "0.0.1"
    res = svc.accept(user_id="u1", role="farmer", documents=docs, method="login_review")
    assert not res.ok and res.status_code == 409 and "current_version" in res.data


def test_cannot_accept_another_roles_document(svc):
    res = svc.accept(user_id="u1", role="vendor", method="login_review",
                     documents=[{"doc_type": "farmer-terms", "version": "1.0.0"}])
    assert not res.ok and res.status_code == 400


def test_invalid_method_and_empty_documents_rejected(svc):
    assert svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="silent").status_code == 400
    assert svc.accept(user_id="u1", role="farmer", documents=[], method="login_review").status_code == 400
    assert svc.accept(user_id="", role="farmer", documents=_docs(svc, "farmer"), method="login_review").status_code == 401


def test_the_deferred_method_cannot_be_chosen_by_the_client(svc):
    res = svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"),
                     method="signup_checkbox_deferred")
    assert not res.ok and res.status_code == 400


def test_failed_write_is_reported_and_recoverable(svc, db):
    db.fail_accept = True
    res = svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    assert not res.ok and "try again" in res.error
    assert res.data["recorded"] == []                       # nothing claimed as recorded
    assert not svc.status("u1", "farmer").data["complete"]
    db.fail_accept = False                                  # retry succeeds
    assert svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review").ok
    assert svc.status("u1", "farmer").data["complete"]


def test_publication_failure_blocks_acceptance(svc, db):
    db.upsert_result = RpcResult(ok=False, status=500, error="x")
    res = svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    assert not res.ok and res.status_code == 503 and db.acceptances == []


# ── database-resident current requirements (migration 0031) ──────────────────
def test_requirements_sync_pushes_the_manifest_set_to_the_database(svc, db):
    assert svc.ensure_requirements_synced().ok
    calls = [(fn, a) for fn, a in db.rpc_calls if fn == "sync_legal_current_requirements"]
    assert len(calls) == 1
    rows = calls[0][1]["p_rows"]
    expected = {(role, k) for role, keys in svc.manifest["role_documents"].items() for k in keys}
    assert {(r["role"], r["doc_type"]) for r in rows} == expected
    tt = next(r for r in rows if r["role"] == "transporter" and r["doc_type"] == "transporter-terms")
    assert tt["version"] == "1.0.1" and tt["last_material_version"] == "1.0.1"
    ck = next(r for r in rows if r["role"] == "farmer" and r["doc_type"] == "cookie-policy")
    assert ck["requires_acceptance"] == "acknowledge"


def test_requirements_sync_runs_once_then_short_circuits(svc, db):
    assert svc.ensure_requirements_synced().ok
    assert svc.ensure_requirements_synced().ok
    assert len([fn for fn, _ in db.rpc_calls if fn == "sync_legal_current_requirements"]) == 1


def test_requirements_sync_failure_is_retryable():
    db = FakeSupabase()
    db.rpc_results["sync_legal_current_requirements"] = RpcResult(ok=False, status=503, error="x")
    svc = LegalService(db)
    res = svc.ensure_requirements_synced()
    assert not res.ok and res.status_code == 503
    db.rpc_results["sync_legal_current_requirements"] = RpcResult(ok=True, data=42)
    assert svc.ensure_requirements_synced().ok        # flag stayed unset, so it retried


def test_enforced_gate_activates_the_database_guard(client, api, monkeypatch, db):
    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    client.post("/api/orders", json={"items": []})
    assert any(fn == "sync_legal_current_requirements" for fn, _ in db.rpc_calls)


def _bumped(svc, doc_type, version, material, last_material=None):
    m = copy.deepcopy(svc.manifest)
    d = m["documents"][doc_type]
    d["version"], d["material"] = version, material
    d["last_material_version"] = last_material or version
    d["sha256"] = "f" * 64
    d["versioned_url"] = f"legal/{doc_type}-{version}.html"
    return LegalService(svc._db, manifest=m)


def test_material_update_triggers_review_but_only_for_that_document(svc):
    svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    new = _bumped(svc, "terms-of-use", "2.0.0", True)
    out = new.status("u1", "farmer").data["outstanding"]
    assert [d["doc_type"] for d in out] == ["terms-of-use"]


def test_non_material_correction_does_not_interrupt_users(svc):
    svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    new = _bumped(svc, "privacy-policy", "1.0.1", False, last_material="1.0.0")
    assert new.status("u1", "farmer").data["complete"]
    # but a brand-new user is still asked for the current version
    assert not new.status("u2", "farmer").data["complete"]


def test_old_acceptance_records_survive_a_new_version(svc, db):
    svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    new = _bumped(svc, "terms-of-use", "2.0.0", True)
    new.accept(user_id="u1", role="farmer", method="login_review",
               documents=[{"doc_type": "terms-of-use", "version": "2.0.0"}])
    versions = sorted(a["version"] for a in db.acceptances if a["doc_type"] == "terms-of-use")
    assert versions == ["1.0.0", "2.0.0"]


# ── sign-up sync ─────────────────────────────────────────────────────────────
def _now():
    return dt.datetime.now(dt.UTC).isoformat()


def test_signup_sync_records_what_was_ticked(svc, db):
    versions = {d["doc_type"]: d["version"] for d in svc.required_documents("farmer")}
    meta = {"legal_signup": {"accepted_at": _now(), "versions": versions}}
    res = svc.sync_signup(user_id="u1", role="farmer", meta=meta, created_at=_now())
    assert res.ok and len(res.data["recorded"]) == len(svc.required_documents("farmer"))
    assert {a["method"] for a in db.acceptances} == {"signup_checkbox_deferred"}


def test_signup_sync_without_a_tick_records_nothing(svc, db):
    res = svc.sync_signup(user_id="u1", role="farmer", meta={}, created_at=_now())
    assert res.ok and res.data["recorded"] == [] and db.acceptances == []
    assert not svc.status("u1", "farmer").data["complete"]       # still asked on the review screen


def test_signup_sync_ignores_old_accounts_and_stale_versions(svc, db):
    versions = {d["doc_type"]: d["version"] for d in svc.required_documents("farmer")}
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=90)).isoformat()
    svc.sync_signup(user_id="u1", role="farmer", created_at=old,
                    meta={"legal_signup": {"versions": versions}})
    assert db.acceptances == []
    svc.sync_signup(user_id="u1", role="farmer", created_at=_now(),
                    meta={"legal_signup": {"versions": {k: "0.0.1" for k in versions}}})
    assert db.acceptances == []


def test_signup_sync_ignores_documents_for_other_roles(svc, db):
    meta = {"legal_signup": {"versions": {"farmer-terms": "1.0.0", "terms-of-use": "1.0.0"}}}
    svc.sync_signup(user_id="u1", role="vendor", meta=meta, created_at=_now())
    assert [a["doc_type"] for a in db.acceptances] == ["terms-of-use"]


# ── marketing consent ────────────────────────────────────────────────────────
def test_accepting_terms_never_records_marketing_consent(svc, db):
    svc.accept(user_id="u1", role="farmer", documents=_docs(svc, "farmer"), method="login_review")
    assert db.marketing == []
    assert svc.get_marketing("u1").data["channels"] == {"sms": False}


def test_marketing_is_separate_optional_and_withdrawable(svc, db):
    assert svc.set_marketing(user_id="u1", channels={"sms": True}, method="settings").ok
    assert svc.get_marketing("u1").data["channels"]["sms"] is True
    assert svc.set_marketing(user_id="u1", channels={"sms": False}, method="settings").ok
    assert svc.get_marketing("u1").data["channels"]["sms"] is False
    assert [m["granted"] for m in db.marketing] == [True, False]        # history kept


def test_marketing_no_duplicate_events_when_unchanged(svc, db):
    svc.set_marketing(user_id="u1", channels={"sms": True})
    res = svc.set_marketing(user_id="u1", channels={"sms": True})
    assert res.ok and res.data["changed"] == [] and len(db.marketing) == 1


@pytest.mark.parametrize("channel", ["email", "whatsapp", "push", "fax"])
def test_only_supported_channels_are_accepted(svc, channel):
    res = svc.set_marketing(user_id="u1", channels={channel: True})
    assert not res.ok and res.status_code == 400


def test_marketing_values_must_be_booleans(svc):
    assert svc.set_marketing(user_id="u1", channels={"sms": "yes"}).status_code == 400


def test_marketing_save_failure_is_reported(svc, db):
    db.fail_insert = True
    res = svc.set_marketing(user_id="u1", channels={"sms": True})
    assert not res.ok and res.status_code == 502


def test_signup_marketing_optin_is_recorded_only_when_ticked(svc, db):
    svc.sync_signup(user_id="u1", role="farmer", created_at=_now(), meta={"marketing_optin": {"sms": False}})
    assert db.marketing == []
    svc.sync_signup(user_id="u2", role="farmer", created_at=_now(), meta={"marketing_optin": {"sms": True}})
    assert [(m["user_id"], m["granted"], m["method"]) for m in db.marketing] == [("u2", True, "signup_checkbox")]


def test_signup_optin_never_overrides_a_later_withdrawal(svc, db):
    meta = {"marketing_optin": {"sms": True}}
    svc.sync_signup(user_id="u1", role="farmer", created_at=_now(), meta=meta)
    svc.set_marketing(user_id="u1", channels={"sms": False})
    svc.sync_signup(user_id="u1", role="farmer", created_at=_now(), meta=meta)
    assert svc.get_marketing("u1").data["channels"]["sms"] is False


def test_the_only_supported_channel_is_sms():
    assert business.MARKETING_CHANNELS == ("sms",)


# ── privacy requests ─────────────────────────────────────────────────────────
def test_privacy_request_without_session_needs_a_valid_email(svc):
    assert svc.create_privacy_request(request_type="access").status_code == 400
    assert svc.create_privacy_request(request_type="access", email="not-an-email").status_code == 400
    res = svc.create_privacy_request(request_type="access", email="a@b.co")
    assert res.ok and res.status_code == 201 and res.data["status"] == "verifying"
    assert res.data["contact"]["email"] == business.SUPPORT_EMAIL


def test_signed_in_request_is_tied_to_the_account(svc, db):
    res = svc.create_privacy_request(request_type="correction", user_id="u1", details="fix my district")
    assert res.ok and db.requests[0]["user_id"] == "u1" and db.requests[0]["identity_verified"] is True


def test_privacy_request_validation(svc):
    assert svc.create_privacy_request(request_type="hack", email="a@b.co").status_code == 400
    assert svc.create_privacy_request(request_type="access", email="a@b.co", details="x" * 2001).status_code == 400


def test_privacy_request_failure_points_to_email(svc, db):
    db.fail_insert = True
    res = svc.create_privacy_request(request_type="access", email="a@b.co")
    assert not res.ok and res.data["email"] == business.SUPPORT_EMAIL


# ═══════════════ API surface ════════════════════════════════════════════════
@pytest.fixture
def client():
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture
def api(monkeypatch, db):
    svc = LegalService(db)
    monkeypatch.setattr(app_mod, "LEGAL_SERVICE", svc)
    return svc


def _as(monkeypatch, uid, role="farmer", created=None):
    def fake():
        app_mod.g.auth_user = {"id": uid, "user_metadata": {"role": role}, "created_at": created or _now()}
        return uid, None
    monkeypatch.setattr(app_mod, "verify_supabase_user", fake)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: (role, "user", ""))


def test_manifest_and_documents_are_public(client, api):
    r = client.get("/api/legal/manifest")
    assert r.status_code == 200 and "terms-of-use" in r.get_json()["documents"]
    for name in ("privacy-policy.html", "terms-of-use.html", "farmer-terms.html", "manifest.json"):
        assert client.get(f"/legal/{name}").status_code == 200


def test_legal_route_cannot_read_other_files(client):
    for bad in ("../app.py", "..%2fapp.py", "x.py", "manifest.json/../../app.py", ".env"):
        assert client.get(f"/legal/{bad}").status_code == 404


@pytest.mark.parametrize("method,path", [
    ("get", "/api/legal/status"), ("post", "/api/legal/accept"), ("post", "/api/legal/sync-signup"),
    ("get", "/api/legal/history"), ("get", "/api/marketing/consent"), ("post", "/api/marketing/consent"),
    ("get", "/api/privacy/requests"),
])
def test_protected_legal_routes_require_a_session(client, api, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401


def test_accept_uses_verified_identity_and_role_not_the_body(client, api, monkeypatch, db):
    _as(monkeypatch, "real-uid", role="supplier")
    docs = [{"doc_type": d["doc_type"], "version": d["version"]} for d in api.required_documents("supplier")]
    r = client.post("/api/legal/accept", json={"documents": docs, "method": "login_review",
                                               "user_id": "attacker", "role": "farmer"})
    assert r.status_code == 200 and r.get_json()["complete"] is True
    assert {a["user_id"] for a in db.acceptances} == {"real-uid"}
    assert {a["role"] for a in db.acceptances} == {"supplier"}


@pytest.mark.parametrize("path,method", [("status", "get"), ("accept", "post"), ("sync-signup", "post")])
def test_legal_bootstrap_never_uses_metadata_or_default_terms(client, api, monkeypatch, db, path, method):
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: ("u1", None))
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("", "user", ""))
    monkeypatch.setattr(app_mod, "supa_get_checked", lambda *a, **kw: ([], ""))
    docs = [{"doc_type": d["doc_type"], "version": d["version"]} for d in api.required_documents("vendor")]
    r = getattr(client, method)(f"/api/legal/{path}", json={"documents": docs, "role": "farmer",
                                                           "method": "login_review"})
    assert r.status_code == 409 and r.get_json()["code"] == "account_type_required"
    assert not db.acceptances


@pytest.mark.parametrize("path,method", [("status", "get"), ("accept", "post"), ("sync-signup", "post")])
def test_legal_routes_share_the_gates_role_failure(client, api, monkeypatch, db, path, method):
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: ("u1", None))
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("", "", "unavailable"))
    r = getattr(client, method)(f"/api/legal/{path}", json={"role": "farmer"})
    assert r.status_code == 503 and r.get_json()["code"] == "legal_check_failed"
    assert not db.acceptances


def test_accept_stale_version_returns_409(client, api, monkeypatch):
    _as(monkeypatch, "u1")
    r = client.post("/api/legal/accept", json={"method": "login_review",
                    "documents": [{"doc_type": "terms-of-use", "version": "0.0.1"}]})
    assert r.status_code == 409


def test_accept_failure_is_an_error_not_a_success(client, api, monkeypatch, db):
    _as(monkeypatch, "u1")
    db.fail_accept = True
    docs = [{"doc_type": d["doc_type"], "version": d["version"]} for d in api.required_documents("farmer")]
    r = client.post("/api/legal/accept", json={"documents": docs, "method": "login_review"})
    assert r.status_code >= 400 and "complete" not in r.get_json()


def test_users_cannot_pick_the_deferred_method(client, api, monkeypatch):
    _as(monkeypatch, "u1")
    docs = [{"doc_type": d["doc_type"], "version": d["version"]} for d in api.required_documents("farmer")]
    r = client.post("/api/legal/accept", json={"documents": docs, "method": "signup_checkbox_deferred"})
    assert r.status_code == 400


def test_sync_signup_endpoint_records_ticked_versions(client, api, monkeypatch, db):
    versions = {d["doc_type"]: d["version"] for d in api.required_documents("farmer")}

    def fake():
        app_mod.g.auth_user = {"id": "u1", "created_at": _now(),
                               "user_metadata": {"role": "farmer", "legal_signup": {"versions": versions},
                                                 "marketing_optin": {"sms": True}}}
        return "u1", None
    monkeypatch.setattr(app_mod, "verify_supabase_user", fake)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("farmer", "user", ""))
    r = client.post("/api/legal/sync-signup", json={})
    body = r.get_json()
    assert r.status_code == 200 and len(body["recorded"]) == len(api.required_documents("farmer")) and body["status"]["complete"] is True
    assert body["marketing"] == ["sms"]


def test_marketing_endpoint_roundtrip_and_unsupported_channel(client, api, monkeypatch):
    _as(monkeypatch, "u1")
    assert client.post("/api/marketing/consent", json={"channels": {"sms": True}}).status_code == 200
    assert client.get("/api/marketing/consent").get_json()["channels"]["sms"] is True
    assert client.post("/api/marketing/consent", json={"channels": {"whatsapp": True}}).status_code == 400
    assert client.post("/api/marketing/consent", json={"channels": {"sms": False}}).status_code == 200
    assert client.get("/api/marketing/consent").get_json()["channels"]["sms"] is False


def test_privacy_request_works_without_a_session(client, api):
    r = client.post("/api/privacy/requests", json={"request_type": "access", "email": "farmer@example.com"})
    assert r.status_code == 201 and r.get_json()["request_id"] == "req-1"


def test_privacy_request_with_bad_token_is_refused_not_silently_anonymous(client, api, monkeypatch):
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: (None, (app_mod.jsonify({"error": "x"}), 401)))
    r = client.post("/api/privacy/requests", headers={"Authorization": "Bearer bad"},
                    json={"request_type": "access", "email": "a@b.co"})
    assert r.status_code == 401


def test_marketing_withdrawal_request_also_turns_sms_off(client, api, monkeypatch, db):
    _as(monkeypatch, "u1")
    api.set_marketing(user_id="u1", channels={"sms": True})
    r = client.post("/api/privacy/requests", json={"request_type": "marketing_withdrawal"},
                    headers={"Authorization": "Bearer t"})
    assert r.status_code == 201
    assert api.get_marketing("u1").data["channels"]["sms"] is False


def test_signed_in_request_ignores_body_user_id(client, api, monkeypatch, db):
    _as(monkeypatch, "real")
    client.post("/api/privacy/requests", headers={"Authorization": "Bearer t"},
                json={"request_type": "access", "user_id": "attacker"})
    assert db.requests[0]["user_id"] == "real"


# ── enforcement gate on protected actions ────────────────────────────────────
class _Orders:
    def create_order(self, **kw):
        from services.orders import OrderResult
        return OrderResult(ok=True, order_ref="R", tracking_code="T", total_price=1, items=[])


def test_gate_is_off_by_default_so_rollout_cannot_lock_everyone_out(client, api, monkeypatch):
    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", False)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    assert client.post("/api/orders", json={"items": []}).status_code == 201


def test_gate_blocks_protected_actions_until_terms_accepted(client, api, monkeypatch, db):
    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    r = client.post("/api/orders", json={"items": []})
    assert r.status_code == 403 and r.get_json()["code"] == "legal_acceptance_required"
    docs = [{"doc_type": d["doc_type"], "version": d["version"]} for d in api.required_documents("farmer")]
    api.accept(user_id="u1", role="farmer", documents=docs, method="login_review")
    assert client.post("/api/orders", json={"items": []}).status_code == 201


def test_gate_cannot_be_bypassed_by_claiming_a_different_role(client, api, monkeypatch):
    _as(monkeypatch, "u1", role="farmer")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    r = client.post("/api/orders", json={"items": [], "role": "admin"})
    assert r.status_code == 403


PROTECTED_ROUTES = [
    ("POST", "/api/uploads"), ("POST", "/api/listings"),
    ("PATCH", "/api/listings/l1/media"), ("PATCH", "/api/listings/l1"),
    ("POST", "/api/service-requests"), ("POST", "/api/orders"),
    ("POST", "/api/orders/o1/status"), ("POST", "/api/requests"),
    ("POST", "/api/offers/o1/accept"), ("POST", "/api/offers/o1/reject"),
    ("POST", "/api/transporter/availability"), ("GET", "/api/transporter/me"),
    ("POST", "/api/transporter/location"), ("GET", "/api/transporter/jobs"),
    ("POST", "/api/transporter/jobs/d1/claim"), ("GET", "/api/transporter/deliveries"),
    ("POST", "/api/transporter/deliveries/d1/status"),
    ("POST", "/api/transporter/deliveries/d1/offer"),
    ("POST", "/api/transporter/deliveries/d1/proof"),
    ("GET", "/api/transporter/earnings"), ("PATCH", "/api/transporter/profile"),
    ("GET", "/api/transporter/dashboard"), ("GET", "/api/orders/o1/receipt"),
    ("GET", "/api/buyer/orders"), ("GET", "/api/buyer/orders/o1/receipt"),
    ("POST", "/api/buyer/orders/o1/receipt/email"),
    ("GET", "/api/user/notifications"), ("POST", "/api/user/notifications/n1/read"),
    ("POST", "/api/user/notifications/read-all"), ("POST", "/api/user/push-subscribe"),
    ("POST", "/api/user/push-subscriptions"), ("POST", "/api/sms-receipt"),
    ("POST", "/api/pay/initiate"), ("GET", "/api/pay/status"),
    ("POST", "/api/pay/card"), ("POST", "/api/pay/card/validate"),
]


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_central_gate_runs_before_each_marketplace_handler(client, api, monkeypatch, method, path):
    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    adapter = app_mod.app.url_map.bind("localhost")
    endpoint, _ = adapter.match(path, method=method)
    called = []

    def handler(**kw):
        called.append(kw)
        return app_mod.jsonify({"reached": True})

    monkeypatch.setitem(app_mod.app.view_functions, endpoint, handler)
    r = client.open(path, method=method, json={"user_id": "another", "role": "admin"})
    assert r.status_code == 403 and r.get_json()["code"] == "legal_acceptance_required"
    assert not called
    api.accept(user_id="u1", role="farmer", documents=_docs(api, "farmer"), method="login_review")
    r = client.open(path, method=method, json={})
    assert r.status_code == 200 and r.get_json()["reached"]
    assert len(called) == 1


@pytest.mark.parametrize("method,path", PROTECTED_ROUTES)
def test_central_gate_requires_authentication_before_consent(client, api, monkeypatch, method, path):
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    r = client.open(path, method=method, json={"user_id": "u1"})
    assert r.status_code == 401


@pytest.mark.parametrize("provider", ["email", "google", "phone"])
@pytest.mark.parametrize("role", ["farmer", "vendor", "hotel", "supplier", "exporter", "transporter"])
def test_provider_signin_is_not_consent_and_auth_is_cached_only_per_request(
        client, api, monkeypatch, provider, role):
    from types import SimpleNamespace

    calls = []
    user = {"id": "u1", "app_metadata": {"provider": provider},
            "user_metadata": {"role": "admin", "legal_accepted": True}}

    def get(url, **kw):
        assert url.endswith("/auth/v1/user")
        calls.append(kw["headers"]["Authorization"])
        return SimpleNamespace(status_code=200, json=lambda: user)

    monkeypatch.setattr(app_mod.requests, "get", get)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: (role, "user", ""))
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    headers = {"Authorization": "Bearer tok"}
    r = client.post("/api/orders", json={"items": []}, headers=headers)
    assert r.status_code == 403
    api.accept(user_id="u1", role=role, documents=_docs(api, role), method="login_review")
    assert client.post("/api/orders", json={"items": []}, headers=headers).status_code == 201
    assert calls == ["Bearer tok", "Bearer tok"]
    assert client.post("/api/orders", json={"items": []}).status_code == 401


@pytest.mark.parametrize("source", ["roles", "farmers", "transporters", "buyers"])
def test_gate_fails_closed_on_authoritative_role_read_error(client, api, monkeypatch, source):
    _as(monkeypatch, "u1", "vendor")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: (
        "", "", "database unavailable" if source == "roles" else ""))
    monkeypatch.setattr(app_mod, "supa_get_checked", lambda table, *a, **kw: (
        (None, "database unavailable") if table == source else ([], "")))
    api.accept(user_id="u1", role="vendor", documents=_docs(api, "vendor"), method="login_review")
    r = client.post("/api/orders", json={"items": []})
    assert r.status_code == 503 and r.get_json()["code"] == "legal_check_failed"


@pytest.mark.parametrize("table,row,role", [
    ("farmers", {"id": "u1"}, "farmer"),
    ("transporters", {"id": "t1"}, "transporter"),
    ("buyers", {"buyer_type": "hotel"}, "hotel"),
    ("buyers", {"buyer_type": "individual"}, "vendor"),
])
def test_gate_uses_guarded_profile_when_no_business_role_exists(client, api, monkeypatch, table, row, role):
    _as(monkeypatch, "u1", "supplier")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("", "user", ""))
    monkeypatch.setattr(app_mod, "supa_get_checked", lambda t, *a, **kw: ([row] if t == table else [], ""))
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    r = client.post("/api/orders", json={"items": []})
    assert r.status_code == 403
    assert {d["doc_type"] for d in r.get_json()["outstanding"]} == {d["doc_type"] for d in _docs(api, role)}
    api.accept(user_id="u1", role=role, documents=_docs(api, role), method="login_review")
    assert client.post("/api/orders", json={"items": []}).status_code == 201


def test_metadata_alone_never_unlocks_the_marketplace(client, api, monkeypatch):
    _as(monkeypatch, "u1", "vendor")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("", "user", ""))
    monkeypatch.setattr(app_mod, "supa_get_checked", lambda *a, **kw: ([], ""))
    api.accept(user_id="u1", role="vendor", documents=_docs(api, "vendor"), method="login_review")
    r = client.post("/api/orders", json={"items": []})
    assert r.status_code == 409 and r.get_json()["code"] == "account_type_required"


def test_acceptance_read_failure_is_not_reported_as_an_empty_history(svc, db):
    db.select_checked = lambda *a, **kw: RpcResult(ok=False, status=503)
    st = svc.status("u1", "farmer")
    assert not st.ok and st.status_code == 503 and "outstanding" not in st.data
    res = svc.sync_signup(user_id="u1", role="farmer", created_at=_now(),
                          meta={"legal_signup": {"versions": {"terms-of-use": "1.0.0"}}})
    assert not res.ok and res.status_code == 503 and db.acceptances == []


@pytest.mark.parametrize("row", [None, {}, {"doc_type": "terms-of-use", "version": 1},
                                 {"doc_type": "terms-of-use", "version": "1.0.0\n"}])
def test_invalid_acceptance_rows_fail_closed(svc, db, row):
    db.select_checked = lambda *a, **kw: RpcResult(ok=True, data=[row])
    assert svc.status("u1", "farmer").status_code == 503


def test_failed_acceptance_read_blocks_marketplace_and_sync(client, api, db, monkeypatch):
    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    db.select_checked = lambda *a, **kw: RpcResult(ok=False, status=503)
    assert client.post("/api/orders", json={}).status_code == 503
    r = client.post("/api/legal/sync-signup", json={})
    assert r.status_code == 503 and "ok" not in r.get_json()


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/legal/manifest"), ("GET", "/legal/index.html"),
    ("GET", "/api/legal/status"), ("POST", "/api/legal/accept"),
    ("POST", "/api/legal/sync-signup"), ("GET", "/api/legal/history"),
    ("GET", "/api/marketing/consent"), ("POST", "/api/marketing/consent"),
    ("GET", "/api/privacy/requests"), ("POST", "/api/privacy/requests"),
    ("POST", "/api/account/delete"), ("POST", "/api/contact"),
    ("GET", "/api/user/account-type"), ("POST", "/api/user/account-type"),
    ("POST", "/api/user/profile"), ("POST", "/api/user/push-unsubscribe"),
    ("DELETE", "/api/user/push-subscriptions"), ("GET", "/api/listings"),
    ("GET", "/api/pay/providers"), ("POST", "/api/pay/webhook/flutterwave"),
    ("POST", "/api/pay/webhook/pesapal"), ("GET", "/api/pay/pesapal/callback"),
    ("POST", "/api/hooks/supabase"), ("GET", "/api/admin/stats"),
])
def test_central_gate_does_not_intercept_exempt_handlers(client, monkeypatch, method, path):
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "verify_supabase_user", lambda: pytest.fail("unexpected authentication"))
    endpoint, _ = app_mod.app.url_map.bind("localhost").match(path, method=method)
    monkeypatch.setitem(app_mod.app.view_functions, endpoint, lambda **kw: app_mod.jsonify({"reached": True}))
    assert client.open(path, method=method, json={}).get_json() == {"reached": True}


def test_preflight_and_unknown_paths_keep_their_normal_responses(client, monkeypatch):
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    assert client.options("/api/orders").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


def test_identity_bootstrap_cannot_retry_a_policy_refusal_with_service_role(client, monkeypatch):
    from types import SimpleNamespace

    _as(monkeypatch, "u1")
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "supa_get", lambda *a, **kw: [])
    writes = []

    def post(url, **kw):
        writes.append(kw["headers"]["Authorization"])
        return SimpleNamespace(status_code=403, json=lambda: {"code": "42501"})

    monkeypatch.setattr(app_mod.requests, "post", post)
    r = client.post("/api/user/profile", headers={"Authorization": "Bearer tok"},
                    json={"full_name": "Sarah Auma", "phone": "+256771234567"})
    assert r.status_code == 503 and writes == ["Bearer tok"]


@pytest.mark.parametrize("failure", ["unconfigured", "http", "json", "shape", "network"])
def test_checked_database_reads_report_failures_without_payloads(monkeypatch, failure):
    from types import SimpleNamespace

    import requests

    from services.supabase_client import SupabaseClient

    db = SupabaseClient("https://example.supabase.co", "" if failure == "unconfigured" else "test-key")

    def get(*a, **kw):
        if failure == "network":
            raise requests.ConnectionError("private details")

        def payload():
            if failure == "json":
                raise ValueError("private details")
            return {} if failure == "shape" else []

        return SimpleNamespace(ok=failure != "http", status_code=503 if failure == "http" else 200, json=payload)

    monkeypatch.setattr(requests, "get", get)
    result = db.select_checked("legal_acceptances")
    assert not result.ok and "private" not in result.error


def test_checked_database_reads_distinguish_successful_empty_result(monkeypatch):
    from types import SimpleNamespace

    import requests

    from services.supabase_client import SupabaseClient

    monkeypatch.setattr(requests, "get", lambda *a, **kw: SimpleNamespace(ok=True, status_code=200, json=lambda: []))
    result = SupabaseClient("https://example.supabase.co", "test-key").select_checked("legal_acceptances")
    assert result.ok and result.data == []


@pytest.mark.parametrize("state,expected", [("expired", 401), ("outstanding", 403), ("accepted", 201)])
def test_route_limit_runs_before_remote_checks_without_double_counting(client, api, monkeypatch, state, expected):
    from types import SimpleNamespace

    reads = []

    def get(url, **kw):
        assert url.endswith("/auth/v1/user")
        reads.append(url)
        return SimpleNamespace(status_code=401 if state == "expired" else 200,
                               json=lambda: {"id": "u1"})

    monkeypatch.setattr(app_mod.requests, "get", get)
    monkeypatch.setattr(app_mod, "_stored_business_role", lambda uid: ("farmer", "user", ""))
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    monkeypatch.setattr(app_mod, "ORDER_SERVICE", _Orders())
    if state == "accepted":
        api.accept(user_id="u1", role="farmer", documents=_docs(api, "farmer"), method="login_review")
    for _ in range(20):
        assert client.post("/api/orders", json={"items": []},
                           headers={"Authorization": "Bearer tok"}).status_code == expected
    assert len(reads) == 20
    assert client.post("/api/orders", json={"items": []},
                       headers={"Authorization": "Bearer tok"}).status_code == 429
    assert len(reads) == 20


@pytest.mark.parametrize("payload", [None, {}, {"error": "private details"}, [None], ["role"]])
def test_malformed_authoritative_role_response_does_not_use_accepted_fallback_terms(
        client, api, monkeypatch, payload):
    from types import SimpleNamespace

    reads = []

    def get(url, **kw):
        reads.append(url)
        if url.endswith("/auth/v1/user"):
            return SimpleNamespace(status_code=200, json=lambda: {"id": "u1", "user_metadata": {"role": "farmer"}})
        if url.endswith("/user_roles"):
            return SimpleNamespace(ok=True, status_code=200, json=lambda: payload)
        return SimpleNamespace(ok=True, status_code=200, json=lambda: [{"id": "u1"}])

    monkeypatch.setattr(app_mod.requests, "get", get)
    monkeypatch.setattr(app_mod, "LEGAL_ENFORCED", True)
    api.accept(user_id="u1", role="farmer", documents=_docs(api, "farmer"), method="login_review")
    r = client.post("/api/orders", json={"items": []}, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 503 and r.get_json()["code"] == "legal_check_failed"
    assert len(reads) == 2 and "private details" not in r.get_data(as_text=True)
