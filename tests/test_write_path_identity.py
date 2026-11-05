# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Which PostgREST role the backend's writes actually run as, pinned by measurement.

Reading the code says something the schema files do not: every write helper builds
`'Authorization': f'Bearer {SUPABASE_KEY}'`, so orders, listings, contact messages
and profile saves reach PostgREST as the unauthenticated `anon` role. That is why
migration 0025 revokes nothing from anon — cutting that role off would sever the
write path rather than close a hole nobody uses.

This file exists so the arrangement cannot change by accident. When the helpers
move onto a real service identity, this test goes red, and the person fixing it is
pointed at the two places that must change together: the anon revoke in the next
migration and the block in tests/sql/identity_all_users.test.mjs that asserts the
hole is still open.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")

# The helpers that write, and the function each one is defined as.
WRITE_HELPERS = ["supa_insert", "supa_update", "supa_delete"]


def helper_body(name: str) -> str:
    """The source of one top-level function, up to the next def or section rule."""
    start = re.search(rf"^def {name}\(", APP, re.M)
    assert start, f"{name} is no longer a top-level function in app.py"
    following = re.search(r"^(?:def |# ── )", APP[start.end():], re.M)
    end = start.end() + (following.start() if following else len(APP))
    return APP[start.start():end]


@pytest.mark.parametrize("name", WRITE_HELPERS)
def test_this_write_helper_runs_as_the_anon_role(name: str):
    body = helper_body(name)
    assert re.search(r"'Authorization': f'Bearer \{SUPABASE_KEY\}'", body), (
        f"{name} no longer sends the publishable key. If it now sends "
        f"SUPABASE_SERVICE_KEY or a caller's token, that write runs as "
        f"service_role or authenticated instead of anon — so 0025's successor can "
        f"revoke anon's writes, and tests/sql/identity_all_users.test.mjs must be "
        f"updated in the same commit."
    )


def test_the_profile_upsert_runs_as_the_anon_role_too():
    """POST /api/user/profile builds its headers inline, not through a helper."""
    route = APP[APP.index("def update_user_profile()"):]
    route = route[:route.index("\n@app.route", 100)]
    assert "f'Bearer {SUPABASE_KEY}'" in route, (
        "the profile upsert no longer sends the publishable key — same follow-up "
        "as above: revoke anon UPDATE on farmers and update the SQL test."
    )


def test_the_service_key_is_only_used_where_it_is_declared_to_bypass_rls():
    """SUPABASE_SERVICE_KEY appears in one helper, for reads.

    A second reader would be fine; a silent `or SUPABASE_KEY` fallback next to a
    write would not — it means the code believes it is writing as service_role and,
    when the environment variable is missing, writes as anon instead.
    """
    uses = re.findall(r"key = SUPABASE_SERVICE_KEY or SUPABASE_KEY", APP)
    assert len(uses) == 1, (
        f"{len(uses)} places fall back from the service key to the publishable key. "
        "A write path that does this cannot tell 'locked down' apart from 'not "
        "configured', which is exactly the ambiguity the next migration has to remove."
    )
    assert "SUPABASE_SERVICE_KEY" not in helper_body("supa_insert")
