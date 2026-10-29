# Copyright (C) 2026 Mugumya Zeal. All rights reserved.

"""Regression: the deploy run must prove what it published, not just exit 0.

On 2026-10-27 a green `Deploy public web app` run published a stale bundle; the
mismatch was found hours later by hashing the live page by hand. `verify_deploy`
fetches the pages back and compares them to the checkout.

Most cases here drive `verify()` with a fake `fetch`, so they assert the decision
(what counts as the same bundle) without depending on a socket. One test serves
a real page over HTTP to prove the wiring end to end.
"""
from __future__ import annotations

import hashlib
import http.server
import pathlib
import threading
import urllib.error

import pytest

from scripts import verify_deploy
from scripts.verify_deploy import main, normalise, page_url, sha256

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = "static/admin.html"


def _committed(relative: str) -> bytes:
    return (ROOT / relative).read_bytes()


def _run(*files, base="https://agribrige.com", attempts="1"):
    return main(["--base", base, "--attempts", attempts, "--delay", "0", *files],
                sleep=lambda _s: None)


def _serving(monkeypatch, body: bytes):
    monkeypatch.setattr(verify_deploy, "fetch", lambda url, timeout: body)


def test_page_url_keeps_the_file_name():
    assert page_url("https://agribrige.com/", PAGE) == "https://agribrige.com/admin.html"


def test_a_page_that_matches_its_commit_passes(monkeypatch, capsys):
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return _committed(PAGE)

    monkeypatch.setattr(verify_deploy, "fetch", fake)
    assert _run(PAGE) == 0
    out = capsys.readouterr().out
    assert "OK   static/admin.html" in out
    assert sha256(normalise(_committed(PAGE)))[:16] in out
    # A cached copy of an older bundle must not be able to answer the check.
    assert seen["url"].startswith("https://agribrige.com/admin.html?v=")


def test_a_page_that_differs_fails_and_names_both_hashes(monkeypatch, capsys):
    _serving(monkeypatch, b"<html>an older bundle</html>")
    assert _run(PAGE) == 1
    out = capsys.readouterr().out
    assert "FAIL static/admin.html" in out
    assert "::error::" in out, "the reason must be readable without a GitHub login"
    assert sha256(b"<html>an older bundle</html>")[:16] in out
    assert sha256(normalise(_committed(PAGE)))[:16] in out


def test_a_crlf_checkout_matches_a_lf_deployment(monkeypatch, capsys):
    """A Windows working copy is CRLF; the CI checkout that gets deployed is LF.
    That difference is not a stale bundle and must not fail the check."""
    _serving(monkeypatch, _committed(PAGE).replace(b"\r\n", b"\n"))
    assert _run(PAGE) == 0
    assert "OK   static/admin.html" in capsys.readouterr().out


def test_the_edge_analytics_tag_does_not_make_a_release_look_stale(monkeypatch, capsys):
    """Cloudflare injects its beacon on a fresh HTML response and not on a cached
    one (measured live: 129,258 vs 129,625 bytes for the same bundle). The tag is
    the edge's, not the release's."""
    beacon = (b'<script type="module" src="https://static.cloudflareinsights.com/'
              b'beacon.min.js/v123"></script>\n')
    _serving(monkeypatch, _committed(PAGE).replace(b"</body>", beacon + b"</body>", 1))
    assert _run(PAGE) == 0
    assert "OK   static/admin.html" in capsys.readouterr().out


def test_any_other_added_script_still_fails(monkeypatch, capsys):
    _serving(monkeypatch, _committed(PAGE).replace(
        b"</body>", b'<script src="https://example.com/other.js"></script></body>', 1))
    assert _run(PAGE) == 1
    assert "FAIL static/admin.html" in capsys.readouterr().out


def test_a_transient_mismatch_is_retried_then_passes(monkeypatch, capsys):
    """A deploy still propagating can answer with the old bytes once."""
    calls = {"n": 0}

    def fake(url, timeout):
        calls["n"] += 1
        return b"<html>previous release</html>" if calls["n"] == 1 else _committed(PAGE)

    monkeypatch.setattr(verify_deploy, "fetch", fake)
    assert _run(PAGE, attempts="3") == 0
    assert calls["n"] == 2, "the stale first response must not have been accepted"
    assert "OK   static/admin.html" in capsys.readouterr().out


def test_an_unreachable_host_fails_instead_of_passing(monkeypatch, capsys):
    def refused(url, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(verify_deploy, "fetch", refused)
    assert _run(PAGE) == 1
    assert "could not be fetched" in capsys.readouterr().out


def test_a_path_outside_static_is_rejected(monkeypatch):
    _serving(monkeypatch, b"")
    with pytest.raises(SystemExit) as excinfo:
        _run("app.py")
    assert excinfo.value.code == 2


def test_a_file_missing_from_the_checkout_fails(monkeypatch, capsys):
    _serving(monkeypatch, b"anything")
    assert _run("static/nope.html") == 1
    assert "no such file in the checkout" in capsys.readouterr().out


def test_hash_matches_hashlib():
    assert sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_the_check_works_over_real_http(capsys):
    """One end-to-end pass: a real server returns the committed page."""
    body = _committed(PAGE)
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        # Two attempts: a local socket reset on this host is retried, not fatal.
        assert _run(PAGE, base=base, attempts="2") == 0
    finally:
        server.shutdown()
        server.server_close()
    assert "OK   static/admin.html" in capsys.readouterr().out
    assert requests and requests[0].startswith("/admin.html?v=")


def test_deploy_workflow_runs_the_check_after_deploy():
    text = (ROOT / ".github/workflows/deploy-cloudflare.yml").read_text(encoding="utf-8")
    assert "scripts/verify_deploy.py" in text, (
        "the deploy workflow never verifies what it published")
    assert "Verify the live bundle is this commit" in text
    # The step must come after the deploy, or it would check the previous release.
    deploy_at = text.index("wrangler@4 deploy")
    step_at = text.index("Verify the live bundle is this commit")
    assert step_at > deploy_at, "verification must run after the deploy step"
    assert "scripts/verify_deploy.py" in text[step_at:]
    assert "static/*.html" in text[step_at:], "no pages are passed to the check"


@pytest.mark.parametrize("name", ["static/admin.html", "static/index.html",
                                  "static/transporter.html", "static/logistics.html"])
def test_every_published_page_can_be_named_by_the_check(name):
    assert (ROOT / name).is_file()
    assert page_url("https://agribrige.com", name).endswith("/" + name.rsplit("/", 1)[-1])
