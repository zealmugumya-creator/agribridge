# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Which PostgREST role each backend write runs as, pinned against the source.

Two different identities leave this file, and the difference is the whole point of
migration 0026.

  * `supa_insert` / `supa_update` / `supa_delete` and most routes send
    `Bearer {SUPABASE_KEY}`. What role that is cannot be settled from the
    repository: `.env.example:15` documents `SUPABASE_KEY` as the SERVICE-ROLE
    key, and `SUPABASE_SERVICE_KEY` is a separate, optional variable. So these
    writes run as `service_role` under the documented configuration — which
    bypasses RLS — and as `anon` if the deploy actually holds the publishable
    key. Measured in tests/sql/self_signup_policies.test.mjs: on the live state
    an `anon` write to `farmers` is already refused, by the absence of an INSERT
    policy, so 0026's `revoke ... from anon` removes no working write path either
    way. That is why 0025 stopped revoking anon and 0026 could.

  * `POST /api/user/profile` writes the caller's OWN row, so it sends the caller's
    verified access token first. PostgREST derives the role from that JWT, which
    means the write is checked by `farmers_owner_insert` (0026) and by the
    `farmers` column grants (0025) rather than sailing past both. Only when that
    answer is 42501 — refused at the caller's own role, which is what an un-migrated
    project returns — does the route retry once with the backend key, because the
    payload is three validated columns keyed to the verified uid and taking signup
    down while a migration waits in a dashboard is not a fix.

This file goes red if either arrangement changes silently. If the profile route
sends `SUPABASE_KEY` first again, 0026's policies become dead code and the
owner's duplicate-account problem keeps its root.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")

# The helpers that write, and the function each one is defined as.
WRITE_HELPERS = ["supa_insert", "supa_update", "supa_delete"]
BACKEND_KEY_RE = r"'Authorization': f'Bearer \{SUPABASE_KEY\}'"


def helper_body(name: str) -> str:
    """The source of one top-level function, up to the next def or section rule."""
    start = re.search(rf"^def {name}\(", APP, re.M)
    assert start, f"{name} is no longer a top-level function in app.py"
    following = re.search(r"^(?:def |# ── )", APP[start.end():], re.M)
    end = start.end() + (following.start() if following else len(APP))
    return APP[start.start():end]


def profile_route() -> str:
    route = APP[APP.index("def update_user_profile()"):]
    return route[:route.index("\n@app.route", 100)]


@pytest.mark.parametrize("name", WRITE_HELPERS)
def test_backend_write_helpers_still_send_the_backend_key(name: str):
    assert re.search(BACKEND_KEY_RE, helper_body(name)), (
        f"{name} no longer sends SUPABASE_KEY. Whichever key replaces it changes "
        f"the role these writes reach PostgREST with, and 0026's anon revoke plus "
        f"the 'service_role writes freely' assertion in "
        f"tests/sql/self_signup_policies.test.mjs are both written against the "
        f"current arrangement."
    )


def test_a_self_profile_write_is_sent_as_the_caller_not_the_backend():
    """The one write in this app that belongs to the person making the request."""
    route = profile_route()
    first = route.index("_save(_bearer_token())")
    assert route.count("_save(") >= 2, (
        "the profile route now makes a single PostgREST call again. That is only "
        "correct if it is the caller's token; if the backend key replaced it, RLS "
        "and 0025's column grants never apply to a self-write and a missing "
        "INSERT policy stays invisible."
    )
    # The fallback exists, but it may only ever be reached behind the 42501 test.
    fallback = route.index("_save(SUPABASE_KEY)")
    assert fallback > first
    assert route.index("if code == '42501'") < fallback, (
        "the backend-key retry is no longer guarded by the 42501 branch, so every "
        "profile save runs as service_role again — the arrangement this stage removed."
    )
    assert not re.search(BACKEND_KEY_RE, route), (
        "the profile route builds an Authorization header from SUPABASE_KEY directly."
    )


def test_the_forwarded_token_is_the_one_gotrue_just_verified():
    """`_bearer_token()` is only this request's own header, and the route must
    validate that header with GoTrue before it reaches the write.

    Reading the header directly would otherwise be a way to send an unvalidated
    token to PostgREST; `auth.uid()` in the policy would then be somebody's guess.
    """
    route = profile_route()
    assert "verify_supabase_user()" in route
    assert route.index("verify_supabase_user()") < route.index("_bearer_token()"), (
        "the profile route builds its PostgREST headers before it verifies the "
        "session — the write would then carry an unverified token."
    )
    check = helper_body("verify_supabase_user")
    assert "/auth/v1/user" in check, (
        "verify_supabase_user no longer asks GoTrue, so nothing vouches for the "
        "token this route forwards."
    )


def test_the_service_key_is_only_used_where_it_is_declared_to_bypass_rls():
    """SUPABASE_SERVICE_KEY appears in one helper, for reads.

    A second reader would be fine; a silent `or SUPABASE_KEY` fallback next to a
    write would not — it means the code believes it is writing as service_role and,
    when the environment variable is missing, writes under whatever the publishable
    key can do.
    """
    uses = re.findall(r"key = SUPABASE_SERVICE_KEY or SUPABASE_KEY", APP)
    assert len(uses) == 1, (
        f"{len(uses)} places fall back from the service key to the publishable key. "
        "A write path that does this cannot tell 'locked down' apart from 'not "
        "configured', which is exactly the ambiguity the next migration has to remove."
    )
    assert "SUPABASE_SERVICE_KEY" not in helper_body("supa_insert")
