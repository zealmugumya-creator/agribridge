# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Guards for static/sw.js — the worker that shows push notifications.

The file once carried two generations of handler side by side. A service worker
delivers an event to *every* listener for it, so each push called
showNotification twice with two different tags and the phone showed two
notifications for one message; a tap then ran both click handlers, one posting
a message type no page has ever listened for. Nothing exercised the worker, so
nothing noticed. These tests pin the properties that make one push equal one
notification, and keep the file from regaining a fetch handler (the stale-code
regression its own header warns about).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SW = ROOT / "static" / "sw.js"
INDEX = ROOT / "static" / "index.html"


def _source() -> str:
    return SW.read_text(encoding="utf-8")


def _listeners(kind: str) -> int:
    return len(re.findall(rf"addEventListener\(\s*['\"]{kind}['\"]", _source()))


def test_one_handler_per_event():
    """Two listeners for one event means two notifications and two click paths."""
    assert _listeners("push") == 1, "sw.js must register exactly one push handler"
    assert _listeners("notificationclick") == 1, \
        "sw.js must register exactly one notificationclick handler"


def test_one_push_shows_one_notification():
    calls = len(re.findall(r"showNotification\s*\(", _source()))
    assert calls == 1, f"one push must produce one notification, found {calls} calls"


def test_the_worker_never_caches_or_intercepts_fetches():
    src = _source()
    assert "addEventListener('fetch'" not in src and 'addEventListener("fetch"' not in src
    assert not re.search(r"caches\.open\s*\(", src)


def test_every_icon_it_names_is_published():
    for path in set(re.findall(r"['\"]/([\w.-]+\.png)", _source())):
        assert (ROOT / "static" / path).exists(), \
            f"sw.js references /{path}, which is not in static/"


def test_the_message_it_posts_on_tap_is_one_the_page_handles():
    posted = set(re.findall(r"postMessage\(\s*\{\s*type:\s*['\"]([\w_]+)['\"]", _source()))
    html = INDEX.read_text(encoding="utf-8")
    for kind in posted:
        assert f"'{kind}'" in html, \
            f"sw.js posts {kind} on tap, but no page listens for it"
