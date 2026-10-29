#!/usr/bin/env python3
# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Fetch the pages this push was supposed to publish and compare them byte-for-byte.

`Deploy public web app` going green only proves wrangler exited 0. On 2026-10-27 a
stale clone published an old bundle under a green run, and the difference was
found hours later by comparing hashes by hand. This closes that gap: the run
fetches the changed pages back from the live host and fails if the bytes served
are not the bytes committed.

    python3 scripts/verify_deploy.py --base https://agribrige.com static/admin.html

Exit 0: every page served exactly the committed file. Exit 1: at least one page
differed, or could not be fetched, after the retries. Exit 2: bad arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIMEOUT_S = 20
USER_AGENT = "agribridge-deploy-verify/1"
# Cloudflare Web Analytics injects this tag into HTML on the way out. A cached
# object predates the injection and a fresh one carries it, so the same bundle
# arrives with and without it (measured: 129,258 bytes from cache, 129,625 from
# origin). It belongs to the edge, not to the release, so it is removed before
# comparing — and nothing else is.
ANALYTICS_TAG = re.compile(rb"<script[^>]*cloudflareinsights\.com[^>]*>\s*</script>\r?\n?")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalise(data: bytes) -> bytes:
    """Git stores LF; a Windows checkout rewrites the working copy to CRLF while
    the deployed bytes come from the CI checkout. Compare content, not the line
    ending the local disk happens to use, and ignore the edge's analytics tag."""
    return ANALYTICS_TAG.sub(b"", data.replace(b"\r\n", b"\n"))


def page_url(base: str, repo_path: str) -> str:
    """Where `static/x.html` is served. The file name is kept, because clean URLs
    (`/admin`) and file URLs (`/admin.html`) answer with the same bytes; keeping
    the name avoids guessing at rewrites for each page."""
    return f"{base.rstrip('/')}/{pathlib.PurePosixPath(repo_path).name}"


def fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        # Ask for the bytes as stored: a compressed body would hash differently.
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def verify(repo_path: str, base: str, attempts: int, delay: float, timeout: float,
           sleep=time.sleep) -> tuple[bool, str]:
    path = (ROOT / repo_path).resolve()
    if not path.is_file():
        return False, f"no such file in the checkout: {repo_path}"
    expected = normalise(path.read_bytes())
    expected_hash = sha256(expected)
    # A per-commit query value keeps a cached copy of an older bundle from
    # answering the check it is meant to fail.
    url = f"{page_url(base, repo_path)}?v={expected_hash[:12]}"

    served = b""
    error = ""
    for attempt in range(1, attempts + 1):
        try:
            served = normalise(fetch(url, timeout))
            if sha256(served) == expected_hash:
                return True, (f"OK   {repo_path}  {len(served)} bytes  "
                              f"sha256 {expected_hash[:16]}")
            error = (f"served sha256 {sha256(served)[:16]} ({len(served)} bytes), "
                     f"the commit has {expected_hash[:16]} ({len(expected)} bytes)")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = f"could not be fetched: {exc}"
        if attempt < attempts:
            sleep(delay)
    return False, f"FAIL {repo_path}  after {attempts} attempt(s): {error}  ({url})"


def main(argv: list[str] | None = None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="site origin, e.g. https://agribrige.com")
    parser.add_argument("--attempts", type=int, default=6,
                        help="fetches per page before failing (default 6)")
    parser.add_argument("--delay", type=float, default=8.0,
                        help="seconds between attempts; Cloudflare needs a moment (default 8)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    parser.add_argument("files", nargs="+", help="paths as they exist in the repo")
    args = parser.parse_args(argv)
    if args.attempts < 1:
        parser.error("--attempts must be at least 1")

    failures = 0
    for repo_path in args.files:
        if not repo_path.replace("\\", "/").startswith("static/"):
            parser.error(f"not under static/: {repo_path}")
        ok, message = verify(repo_path, args.base, args.attempts, args.delay,
                             args.timeout, sleep=sleep)
        print(message)
        if not ok:
            failures += 1
            # Job logs need a GitHub login; an annotation does not.
            print(f"::error::{message}")
    if failures:
        print(f"{failures} of {len(args.files)} page(s) are not what this commit contains.")
        return 1
    print(f"{len(args.files)} page(s) verified against {args.base}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
