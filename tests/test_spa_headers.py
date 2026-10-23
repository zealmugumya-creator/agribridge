# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Guards for static/_headers — the response headers the Worker actually sends.

The SPA has declared nosniff, SAMEORIGIN and a Referrer-Policy since before this
test existed, but only as <meta http-equiv=…> tags. Two of them cannot work that
way: browsers ignore X-Frame-Options delivered in meta, and CSP forbids
frame-ancestors in a meta-delivered policy (and ignores X-Frame-Options there
too). They were inert until delivered as headers. This test fails if the header
file goes missing, loses a universal header, or drifts from the meta CSP — a
drift where the header is *narrower* would break the app in production while
every server-side test still passed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADERS = ROOT / "static" / "_headers"
INDEX = ROOT / "static" / "index.html"

UNIVERSAL = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=15552000; includeSubDomains",
}


def _parse(text: str) -> dict[str, dict[str, str]]:
    """Parse a Cloudflare _headers file.

    Pattern lines start at column 0; every header line under them must be
    indented, which is also the syntax Cloudflare requires. This therefore
    doubles as a syntax check: an unindented header line is read as a path.
    """
    blocks: dict[str, dict[str, str]] = {}
    pattern = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[:1] in (" ", "\t"):
            assert pattern is not None, f"header line before any path pattern: {raw!r}"
            name, _, value = raw.strip().partition(":")
            assert name.strip(), f"header line with no name: {raw!r}"
            blocks[pattern][name.strip()] = " ".join(value.split())
        else:
            pattern = raw.strip()
            assert pattern.startswith("/"), f"path pattern must start with /: {pattern!r}"
            blocks.setdefault(pattern, {})
    return blocks


def _blocks() -> dict[str, dict[str, str]]:
    assert HEADERS.exists(), "static/_headers is missing — the Worker sends no custom headers"
    return _parse(HEADERS.read_text(encoding="utf-8"))


def _split_directives(csp: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0].lower()] = " ".join(tokens[1:])
    return out


_META_CSP = re.compile(
    r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"', re.IGNORECASE
)


def _meta_csp() -> str:
    matches = _META_CSP.findall(INDEX.read_text(encoding="utf-8"))
    assert len(matches) == 1, f"expected exactly one CSP meta in index.html, found {len(matches)}"
    return matches[0]


def test_headers_file_is_present_and_indented() -> None:
    blocks = _blocks()
    assert "/*" in blocks, "no /* block, so no path is protected"


def test_universal_headers_apply_to_every_path() -> None:
    got = _blocks()["/*"]
    for name, value in UNIVERSAL.items():
        assert got.get(name) == value, f"/* sends {got.get(name)!r}, expected {value!r}"


def test_csp_is_delivered_for_both_entry_paths() -> None:
    """`/` is what a browser asks for; /index.html is what the asset is named."""
    blocks = _blocks()
    for path in ("/", "/index.html"):
        assert "Content-Security-Policy" in blocks[path], f"{path} sends no CSP header"
        assert blocks["/"]["Content-Security-Policy"] == blocks["/index.html"][
            "Content-Security-Policy"
        ], "CSP differs between / and /index.html — one of them is stale"


def test_delivered_csp_keeps_every_meta_directive() -> None:
    """The browser enforces the intersection of header and meta, so a narrower
    header directive silently breaks features that work in every test."""
    delivered = _split_directives(_blocks()["/index.html"]["Content-Security-Policy"])
    declared = _split_directives(_meta_csp())
    for name, sources in declared.items():
        assert name in delivered, f"meta declares {name}, the header does not"
        assert delivered[name] == sources, (
            f"{name}: header {delivered[name]!r} != meta {sources!r}"
        )


def test_delivered_csp_adds_frame_ancestors_that_meta_cannot_express() -> None:
    delivered = _split_directives(_blocks()["/index.html"]["Content-Security-Policy"])
    assert delivered.get("frame-ancestors") == "'self'"
    assert "frame-ancestors" not in _split_directives(_meta_csp())


def test_pages_without_a_meta_csp_are_not_given_a_stricter_one() -> None:
    """admin.html and logistics.html declare no CSP. Shipping one here would be a
    new, untested policy for screens the owner uses daily; they get only the
    universal headers from /*."""
    blocks = _blocks()
    for page in ("/admin.html", "/logistics.html"):
        assert "Content-Security-Policy" not in blocks.get(page, {}), page
    for page in ("/admin.html", "/logistics.html"):
        assert "X-Frame-Options" not in blocks.get(page, {}), (
            f"{page} overrides the universal X-Frame-Options"
        )


def test_meta_only_headers_are_still_declared_in_the_page() -> None:
    """Permissions-Policy has no header block yet; keep the meta honest."""
    head = INDEX.read_text(encoding="utf-8")[:4000].lower()
    for name in ("x-content-type-options", "x-frame-options", "referrer-policy"):
        assert f'http-equiv="{name}"' in head, f"index.html no longer declares {name} in meta"
