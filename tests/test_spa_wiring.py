# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Every onclick handler in the SPA names a function that exists.

The owner reported that the Sign Out button did nothing. It was not a session bug:
the button called `signOut()`, and the only logout function in the file is
`logOut()` (static/index.html:10292), so the click raised a ReferenceError and the
session stayed signed in. Measured across the whole file, that was the only dead
handler among 176 — which is exactly why it survived: nothing ever looked.

This test looks. It parses the inline handlers rather than the script body, so it
catches the wiring mistake rather than the syntax mistake, and it deliberately
fails on the specific name that was broken so the fix cannot be reverted by a
find-and-replace.
"""
from __future__ import annotations

import re
from pathlib import Path

SPA = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(
    encoding="utf-8"
)

# Called bare in a handler and provided by the browser, not by this file.
GLOBALS = {
    "alert", "confirm", "prompt", "parseInt", "parseFloat", "isNaN", "isFinite",
    "encodeURIComponent", "decodeURIComponent", "fetch", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval", "structuredClone",
    "require", "JSON", "Math", "Date", "Array", "Object", "String", "Number",
    "Boolean", "Set", "Map", "Event", "CustomEvent", "FormData", "Blob", "URL",
    "navigator", "document", "window", "localStorage", "sessionStorage", "location",
    "console", "crypto", "screen",
}

KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "new", "do",
    "else", "try", "void", "in", "of", "delete", "instanceof",
}


def _handler_bodies() -> list[str]:
    return [m.group(1) for m in re.finditer(r'onclick="([^"]*)"', SPA)]


def _called_names(bodies: list[str]) -> set[str]:
    """Bare call targets only — `foo.bar()` names a method, not a missing function.

    The dot or bracket in front of the name is captured with it and discarded, so
    `.click()` and `.split()` never enter the set.
    """
    names: set[str] = set()
    for body in bodies:
        for match in re.finditer(r"([.\)\]]?)([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", body):
            if match.group(1):
                continue
            names.add(match.group(2))
    return names


def _defined_names() -> set[str]:
    names: set[str] = set()
    for m in re.finditer(r"(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", SPA):
        names.add(m.group(1))
    for m in re.finditer(
        r"(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?"
        r"(?:function|\([^)]*\)\s*=>|[A-Za-z_$])",
        SPA,
    ):
        names.add(m.group(1))
    for m in re.finditer(r"window\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=", SPA):
        names.add(m.group(1))
    return names


def test_no_inline_handler_calls_a_function_that_does_not_exist():
    undefined = sorted(
        _called_names(_handler_bodies()) - _defined_names() - GLOBALS - KEYWORDS
    )
    assert not undefined, (
        f"onclick handlers call {undefined}, which are defined nowhere in "
        f"static/index.html. The click throws and the button appears dead — the "
        f"Sign Out button did exactly that until this test existed."
    )


def test_the_sign_out_button_is_wired_to_the_function_that_logs_out():
    """The reported bug, pinned by name rather than by the general rule above."""
    handlers = _handler_bodies()
    assert not any(re.search(r"\bsignOut\s*\(", h) for h in handlers), (
        "something calls signOut() again. There is no such function: the logout "
        "path is logOut(), which signs out of Supabase, clears ab_user and the "
        "basket, and updates the nav."
    )
    logout_buttons = [h for h in handlers if "logOut(" in h]
    assert logout_buttons, "no handler calls logOut(), so nothing can sign out"


def _logout_body() -> str:
    body = SPA[SPA.index("async function logOut()"):]
    return body[:body.index("\n}", body.index("{")) + 2]


def test_the_logout_function_actually_ends_the_session():
    """A button that calls a function is not the same as a session that ends.

    logOut() has to reach Supabase and drop the stored identity, or the next page
    load signs the person straight back in.
    """
    body = _logout_body()
    assert "auth.signOut()" in body, "logOut() no longer ends the Supabase session"
    assert "ab_user" in body, "logOut() no longer clears the stored identity"
    assert "currentUser=null" in body.replace(" ", ""), (
        "logOut() leaves currentUser set, so the app still believes someone is "
        "signed in until the page reloads"
    )


def test_a_stale_session_can_still_be_signed_out_of():
    """The awaited signOut() is the first line of the teardown, so a rejection there
    used to abandon everything after it — nav, basket, stored identity and all.

    That is the state a person is in when their refresh token has already been
    revoked (another device signed into the same account, or the session simply
    expired while they were away), which is also the state the owner is describing
    when they say the old accounts are inaccessible. Getting out must not depend on
    the session still being healthy, so the call is tried and the teardown runs
    either way.
    """
    body = _logout_body()
    call = body[body.index("if(sbAuth)"):]
    call = call[:call.index("currentUser=null")]
    assert "try{" in call and "catch(e){}" in call, (
        f"signOut() is awaited unprotected again: {call.strip()[:80]!r}"
    )
    assert body.index("catch(e){}") < body.index("currentUser=null"), (
        "the guard has to sit around the call, not after it"
    )


def test_signing_out_leaves_the_screen_only_the_signed_in_have():
    """Half of 'the sign out button doesn't work' was that nothing visibly changed.

    updateNavUser() swaps two small buttons; a farmer standing on their own dashboard
    kept the dashboard — their listings, their orders, their earnings — with no
    session behind it. The teardown has to close the panel they clicked in and
    repaint a page a guest can be shown.
    """
    body = _logout_body()
    assert "closeModal('settingsModal')" in body, (
        "signing out from Settings leaves the account panel open on a signed-out app"
    )
    assert "showPage('home')" in body, (
        "the person stays on the page their role made, after the role is gone"
    )
    assert "showPage('home')" in body[body.index("currentUser=null"):], (
        "the repaint has to come after the identity is dropped, or it renders for "
        "the person who just signed out"
    )

