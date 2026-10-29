# Copyright (C) 2026 zealmugumya-creator. All rights reserved.

"""Browser tests for the admin Transport pricing screen.

The real static/admin.html runs in headless Chromium against fixture endpoints.
The quote arithmetic is stubbed here because the real one lives in Postgres
(0020 transport_quote); what these tests pin is the console's behaviour around
it: an empty rate table must not produce a price, a saved rate must reach the
calculator, and a quote must be recordable as a delivery's agreed fee with the
inputs that produced it. No production data is read or written.
"""
from __future__ import annotations

import functools
import http.server
import json
import mimetypes
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
API = "https://agribridge-1-og7a.onrender.com"
NOW = "2026-10-29T09:00:00Z"

STATE: dict = {}
WRITES: list = []


def _reset():
    STATE.clear()
    STATE.update({
        "rates": [],
        # Just enough of a row for the select label and the fee column.
        "deliveries": [{"id": "d1", "tracking_code": "TRK-1", "pickup_location": "Kasese",
                        "delivery_location": "Kampala", "transport_fee_ugx": None,
                        "status": "scheduled"}],
    })
    del WRITES[:]


def _quote(body):
    vehicle = str(body.get("vehicle_type") or "")
    rate = next((r for r in STATE["rates"] if r["vehicle_type"] == vehicle), None)
    if not rate:
        # The server's own sentence, which the console must print unchanged.
        return {"status": "not_configured",
                "message": "No transport rates are configured for " + (vehicle or "this vehicle") +
                           ". An administrator must enter them before a fee can be quoted."}
    km = float(body.get("distance_km") or 0)
    kg = float(body.get("weight_kg") or 0)
    base = rate["base_fee_ugx"]
    distance = round(km * rate["ugx_per_km"])
    weight = round(kg * rate["ugx_per_kg"])
    subtotal = base + distance + weight
    night = round(subtotal * rate["night_surcharge_pct"] / 100) if body.get("night") else 0
    return {"status": "ok", "currency": "UGX", "base_fee": base,
            "distance_component": distance, "weight_component": weight,
            "night_surcharge": night, "total": subtotal + night,
            "inputs": {"vehicle_type": vehicle, "distance_km": km, "weight_kg": kg,
                       "night": bool(body.get("night"))},
            "rates_snapshot": {"base_fee_ugx": base, "ugx_per_km": rate["ugx_per_km"],
                               "ugx_per_kg": rate["ugx_per_kg"],
                               "night_surcharge_pct": rate["night_surcharge_pct"]}}


def _handle(route, request):
    cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*",
            "access-control-allow-methods": "GET,POST,PATCH,PUT,DELETE,OPTIONS"}
    url = urlparse(request.url)
    host, path, method = url.netloc, url.path, request.method
    if host.startswith("127.0.0.1"):
        rel = unquote(path).lstrip("/") or "admin.html"
        target = (ROOT / "static" / rel).resolve()
        if not str(target).startswith(str((ROOT / "static").resolve())) or not target.is_file():
            return route.fulfill(status=404, body="not found")
        return route.fulfill(status=200, path=str(target),
                             content_type=mimetypes.guess_type(target.name)[0] or "text/plain")
    if method == "OPTIONS":
        return route.fulfill(status=204, headers=cors, body="")
    if host != urlparse(API).netloc:
        return route.fulfill(status=404, body="not stubbed")

    body = {}
    if request.post_data:
        try:
            body = json.loads(request.post_data)
        except ValueError:
            body = {}

    def send(payload, status=200):
        return route.fulfill(status=status, headers=dict(cors, **{"content-type": "application/json"}),
                             body=json.dumps(payload))

    if path == "/api/admin/stats":
        return send({"farmers": 0, "buyers": 0, "orders": 0, "listings": 0, "animals": 0,
                     "supplies": 0, "transporters": 0, "requests": 0, "offers": 0,
                     "cold_storage": 0, "cold_transport": 0, "loans": 0, "deliveries": 0})
    if path == "/api/admin/transport-rates" and method == "GET":
        return send({"currency": "UGX", "rates": STATE["rates"], "count": len(STATE["rates"])})
    if path.startswith("/api/admin/transport-rates/") and method == "PUT":
        key = unquote(path.rsplit("/", 1)[-1]).lower()
        row = {"vehicle_type": key, "base_fee_ugx": body.get("base_fee_ugx"),
               "ugx_per_km": body.get("ugx_per_km"), "ugx_per_kg": body.get("ugx_per_kg"),
               "night_surcharge_pct": body.get("night_surcharge_pct", 0),
               "note": body.get("note") or None, "updated_at": NOW}
        STATE["rates"] = [r for r in STATE["rates"] if r["vehicle_type"] != key] + [row]
        WRITES.append(("rate", key, body))
        return send({"ok": True, "vehicle_type": key, "currency": "UGX"})
    if path == "/api/admin/transport-quote" and method == "POST":
        WRITES.append(("quote", None, body))
        return send(_quote(body))
    if path == "/api/admin/deliveries" and method == "GET":
        return send(STATE["deliveries"])
    if path.startswith("/api/admin/deliveries/") and path.endswith("/fee") and method == "POST":
        WRITES.append(("fee", path.split("/")[4], body))
        return send({"ok": True, "transport_fee_ugx": body.get("amount_ugx"),
                     "fee_basis": body.get("basis"),
                     "disclosed_to_buyer": body.get("disclose_to_buyer")})
    if path == "/api/admin/orders":
        return send([])
    return send([])


@pytest.fixture(scope="module")
def site():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "static"))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as chromium_error:
            try:
                b = p.chromium.launch(channel="chrome")
            except Exception as chrome_error:
                pytest.skip(f"chromium unavailable: {chromium_error}; Chrome fallback: {chrome_error}")
        yield b
        b.close()


@pytest.fixture
def panel(browser, site):
    _reset()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                              service_workers="block", bypass_csp=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    ctx.route("**/*", _handle)
    page.goto(site + "/admin.html", wait_until="domcontentloaded")
    # The sign-in form is not what this test is about; reveal the console the
    # same way a successful sign-in would, then open the section by its tab.
    page.evaluate("""() => {
      document.getElementById('login').classList.add('hidden');
      document.getElementById('dash').classList.remove('hidden');
      showTab('pricing');
    }""")
    page.wait_for_selector("#panel .card h3", timeout=8000)
    yield page, errors
    ctx.close()


def _save_rate(page, vehicle, base, per_km, per_kg, night="0"):
    page.fill("#p_vehicle", vehicle)
    page.fill("#p_base", str(base))
    page.fill("#p_km", str(per_km))
    page.fill("#p_kg", str(per_kg))
    page.fill("#p_night", night)
    # text-is, not a loose text match: "Saving replaces…" below the button also
    # contains the word, and a loose match would click the paragraph instead.
    page.click("#panel button:text-is('Save rate')")
    page.wait_for_function(
        f"document.querySelector('#panel').innerText.includes('UGX {base:,}')")


def _calculate(page):
    # The card subtitle "calculated by the database" contains "calculate"; only
    # the exact button label is unambiguous.
    page.click("#panel button:text-is('Calculate')")


def test_the_tab_opens_and_an_empty_table_invents_no_price(panel):
    page, errors = panel
    text = page.inner_text("#panel")
    assert "No vehicle rate is configured" in text
    # With no rate there is no calculator at all, so no number can be shown.
    assert "A quote needs at least one saved rate" in text
    assert "UGX 0" not in text
    assert not errors, errors


def test_a_saved_rate_appears_in_the_table_and_drives_the_calculator(panel):
    page, errors = panel
    _save_rate(page, "lorry", 50000, 1500, 50, "10")

    text = page.inner_text("#panel")
    assert "lorry" in text and "UGX 50,000" in text and "UGX 1,500" in text
    assert "A quote needs at least one saved rate" not in text

    page.fill("#q_km", "120")
    page.fill("#q_kg", "800")
    page.check("#q_night")
    _calculate(page)
    page.wait_for_function(
        "document.querySelector('#q_result').innerText.includes('UGX 297,000')", timeout=8000)
    # 50,000 + 120*1,500 + 800*50 = 270,000, plus 10% night = 297,000.
    result = page.inner_text("#q_result")
    for label in ("Base fee", "Distance component", "Weight component", "Night surcharge", "Total"):
        assert label in result
    assert not errors, errors


def test_a_quote_is_recorded_as_the_agreed_fee_with_its_inputs(panel):
    page, errors = panel
    _save_rate(page, "lorry", 50000, 1500, 50, "0")
    page.fill("#q_km", "100")
    page.fill("#q_kg", "500")
    _calculate(page)
    page.wait_for_function(
        "document.querySelector('#q_result').innerText.includes('UGX 225,000')", timeout=8000)

    page.select_option("#q_delivery", "d1")
    page.check("#q_disclose")
    page.click("#q_result button:text-is('Record as agreed fee')")
    for _ in range(40):
        if any(w[0] == "fee" for w in WRITES):
            break
        page.wait_for_timeout(100)
    fees = [w for w in WRITES if w[0] == "fee"]
    assert fees, "no fee was written"
    _, delivery_id, body = fees[0]
    assert delivery_id == "d1"
    assert body["amount_ugx"] == 225000
    # Recorded as calculated, with the inputs that produced the figure.
    assert body["basis"] == "calculated"
    assert body["quote_inputs"]["distance_km"] == 100
    assert body["quote_inputs"]["weight_kg"] == 500
    assert body["disclose_to_buyer"] is True
    assert not errors, errors


def test_a_rate_the_server_has_not_configured_is_printed_verbatim(panel):
    page, _errors = panel
    # The calculator only exists once a rate is saved, so make the container
    # real first, then force the server branch the UI must pass through untouched.
    _save_rate(page, "lorry", 50000, 1500, 50, "0")
    page.evaluate("""() => {
      PRICING.quote = {status:'not_configured',
        message:'No transport rates are configured for lorry. An administrator must enter them before a fee can be quoted.'};
      document.getElementById('q_result').innerHTML = quoteHtml();
    }""")
    text = page.inner_text("#q_result")
    assert "No transport rates are configured for lorry" in text
    assert "UGX" not in text
    assert "Record as agreed fee" not in text
