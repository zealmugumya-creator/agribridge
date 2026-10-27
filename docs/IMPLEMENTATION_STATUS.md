# AgriBridge — Implementation Status

> **2026-10-22 update:** The baseline below is the original Phase 1 audit. Since
> then: `/api/pay/initiate` and the SMS/admin/order update paths are session- and
> ownership-checked; migration 0013 (ledger) and 0014 (internal-table lockdown)
> are applied; migration 0015 (APPLIED live — `orders.source_ref` returns 200,
> verified 2026-10-22) makes
> `create_order_atomic` category-aware
> (livestock/supplier pricing and attribution), and the SPA no longer inserts or
> updates orders directly. Migration 0016 adds farmer settlements in code:
> delivered+paid orders create one pending payout, and
> `POST /api/admin/payouts/<id>/settle` posts `settlement:<payout_id>` only with
> an operator-supplied provider reference and a posted receipt. Migration 0017
> adds Pesapal API 3.0 hosted checkout and recurring-charge recording; recurring
> money posts to `SUSPENSE_OVERPAYMENT`, never to a farmer. The SPA also offers
> Google sign-in through Supabase OAuth, with a locked onboarding modal for
> phone/role/district/terms. **Live schema and deployment status (verified
> 2026-10-22 by read-only PostgREST probes and public endpoint probes):** 0002,
> 0016 and 0017 are applied; `payment_gateway_state`, `payment_subscriptions` and
> `subscription_charges` exist and reject `anon` with `42501`. Push `9123b38`
> deployed the API on Render and the SPA via the `Deploy public web app`
> workflow. **0015 is now applied** — `orders.source_ref` returns HTTP 200 — so
> livestock/supplier checkout runs against the category-aware function.
> Push `6077883` restores a safe Bank Transfer method (always offered, real
> account details only from `BANK_TRANSFER_ACCOUNTS`, orders unpaid until an
> admin confirms via `POST /api/admin/orders/<id>/verify-bank-transfer`) and
> fixes duplicate Discord `#signups` alerts (fires once per account). The API on
> Render serves this (live `providers.bank = true`). The Cloudflare auto-deploy
> had **silently skipped** (green run, deploy step skipped), so the SPA has since
> been published from an authenticated workstation each time.
>
> **Deploy automation, now honest and working (2026-10-23):** `Deploy public web
> app` can no longer pass while publishing nothing — it exits 1 when a secret is
> missing, and on any wrangler failure it captures the output and republishes the
> reason as a check annotation plus the run summary. Both
> `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` exist in GitHub. After an
> invalid-token phase (`Authentication error [10000]` / `Invalid access token
> [9109]`), the owner replaced the token with one scoped to **Specified Workers →
> agribridge → Individual Workers Editor**; manual run `39859133726` succeeded
> and published Worker version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`. CI
> publishing is restored. Cloudflare *Workers Builds* was disconnected on
> 2026-10-23 so the Actions workflow is the only publisher
> (`docs/OPERATIONS_SETUP.md` §11, `docs/LIVE_BASELINE_AUDIT.md` §20).
>
> **Buyer-UI redesign (`74879f8`/`20f45f2`) merged red and was repaired in
> `db9d92f`:** the redesign had reintroduced the retired `support@agribridge.ug`
> contact, broken the pinned ruff gate, made 21 tests fail, and — most
> importantly — rewrote `syncPayMethods()` to read a provider cache it never
> fetched, so server-enabled payment options (Pesapal) and configured bank
> accounts could stay hidden on a fresh page load. Fixed and confirmed in
> **production** (the live page now populates `window._payProviders` on first
> checkout render; Bank tile visible, Pesapal correctly hidden while unconfigured).
> Live SPA = wrangler version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`, published
> by CI run `39859133726` from commit `bb2cd7a`; live HTML is the Git blob of
> `static/index.html` (sha256 `439306a7…a19a13`; content-identical to the
> workstation file after CRLF normalisation).
> Current verified suite: **447 Python tests** (including 28 Playwright browser
> tests) and 129 SQL assertions; CI green on `db9d92f`, `30f8784` and `bb2cd7a`;
> the Stage 1 bundle is **published by the restored CI workflow and verified
> live** (see `docs/LIVE_BASELINE_AUDIT.md` §20); see `docs/TEST_RESULTS.md`.
>
> **Pesapal is now configured live (2026-10-22).** The owner added consumer
> credentials and `PESAPAL_ENV=live` in Render; `/api/pay/providers` returns
> `"pesapal": true`, `/health/live` is 200, and both Pesapal routes answer
> (`/api/pay/webhook/pesapal` 200, `/api/pay/pesapal/callback` 302). While
> tracing that config we found a **latent defect**: a truncated
> `PESAPAL_CALLBACK_BASE_URL` passed `_safe_ipn_url` (any https host is accepted,
> even `agribridge-1-`) and would have registered a dead IPN endpoint — money
> taken, order never marked paid, no ledger journal. `app.py` now resolves the
> base URL through `_resolve_pesapal_base_url()`, which requires a dotted
> hostname (localhost exempt), logs `pesapal.callback_base_url_rejected` with the
> fallback used, and otherwise uses `PESAPAL_BASE_URL_DEFAULT`. 5 regression
> tests cover it. Owner must still confirm the Render value is deleted or
> complete. **No real Pesapal payment has been made**, so the notify → verify →
> mark-paid → ledger chain stays unverified in production (Rule 11). Card and
> Flutterwave MTN remain unconfigured; `bank_accounts: []` until
> `BANK_TRANSFER_ACCOUNTS` is set. Note the Render build log installs `cp314`
> wheels while `render.yaml` pins `3.12.7` (CI uses 3.12.7) — see
> `docs/LIVE_BASELINE_AUDIT.md` §18.
> **Live-baseline audit:** `docs/LIVE_BASELINE_AUDIT.md` (2026-10-22) records the
> evidence-based production state, the first verified blocker (deploy automation
> reporting green while publishing nothing) and its fix (§13–§17).

## Stage 1 — Buyer storefront (2026-10-22) — DEPLOYED AND VERIFIED LIVE

The first slice of the buyer-interface work, in the owner's stated order:
**homepage → product cards → product details → shopping cart**. Checkout,
payment integration and delivery tracking are the next stages; this stage only
guards the existing checkout against out-of-stock lines. The admin panel,
financial ledger, API, database and dependencies are untouched.

**Changed files:** `static/index.html` (+987/−289) and the new
`tests/e2e/test_buyer_ui.py`. The Capacitor shell wraps the same HTML, so it
picks the change up when a new bundle is built.

- **Home** — real listings and stats, honest empty/error states, real rated
  farmers only, working search overlay and category/location filters (12
  districts persisted in `localStorage`), and a **Browse as guest** path out of
  the first-visit splash, which previously forced sign-up.
- **Cards** — real image/emoji fallback, discount and struck original price,
  stock/minimum/farmer/district, stars only from `farmer_ratings`, keyboard
  activation, and Add disabled when the listing cannot be bought.
- **Details** — real fields and gallery, data-backed ratings, conditional
  verification and delivery claims, out-of-stock actions disabled, normalised
  descriptions, related products.
- **Cart** — `localStorage` persistence, re-hydrated price/stock on every
  render, quantity bounded by stock and minimum, unavailable lines flagged,
  totals equal the item subtotal, delivery shown as "Arranged after
  confirmation" (orders store no delivery fee — `services/finance.py:25`), and
  checkout blocked while an unavailable line remains.
- **Signed-in buyers** — `applyRoleUI()` now lands a `vendor` on the buyer home
  instead of the market page.

Repeated browser runs also exposed two real navigation races, both fixed and
pinned: a late `loadHomeStats()` reset a category the buyer had just chosen
(`window._homeCat` is now re-applied), and a delayed guest role refresh at 300 ms
closed a product detail page (`showGuestLanding()` no longer steals navigation
when a page is already open).

**Verification:** 6 new Playwright tests (deterministic fixtures, real HTML;
5 consecutive green runs, 12.1–15.4 s; all 28 browser tests green twice);
447 Python tests pass (441 baseline + 6, 66.09 s); ruff clean under local 0.16.10
and CI-pinned 0.8.4; `node --check` passes all four inline script blocks; a
51/51 ad-hoc browser harness ran at mobile/desktop widths against the live
Supabase project **read-only** (no order placed, no row written).

**Production status: PUBLISHED BY CI AND VERIFIED.** The work is commit
`30f8784` on `main`; CI (`Backend lint + type + tests`, `Migration 0007`) is
green. The `Deploy public web app` workflow is working again: after the owner
replaced `CLOUDFLARE_API_TOKEN` with a token scoped to **Specified Workers →
agribridge → Editor**, manual run `39859133726` succeeded and published Worker
version `a6a1d85b-f203-4e90-8dd3-b405455c32c4`. Both
`https://agribrige.com/` and the workers.dev host serve the Git blob of
`static/index.html` at `bb2cd7a`: 752,146 bytes, sha256
`439306a7…a19a13`, content-identical to the workstation file after CRLF
normalisation, with all Stage 1 markers. A production browser smoke test went
landing → Launch app → splash → Browse as guest → home cards → category filter →
detail → add to cart → cart, with honest totals and **zero page errors** (no
order placed). Previous version `bb9e65fb-974d-4381-8bf0-c78191a2e6d0` is the
rollback target. Workers Builds was disconnected on 2026-10-23 so only the
GitHub Actions workflow publishes the Worker; see `docs/LIVE_BASELINE_AUDIT.md` §20.

**Residual / out of scope:** cart is device-local (no server cart); promo codes
remain deliberately absent; the legacy B2B invoice generator still hardcodes a
UGX 25,000 delivery and 6% commission and submits nothing — flagged, not
endorsed; checkout/payment/delivery-tracking behaviour is Stage 2/3 work.

---

## Stage F1 — Farmer dashboard (2026-10-23) — BUILT, TESTED AND DEPLOYED

The first slice of the farmer interface, in the owner's stated order: **home
dashboard first, then the screens behind it**. Five tabs — Home · Produce ·
Orders · Earnings · Profile — with transport and every other tool reachable from
the header ☰ sheet rather than a sixth tab.

**Changed files:** `static/index.html` (+746/−44) and the new
`tests/e2e/test_farmer_ui.py`. No migration, no RLS change, no backend change,
no new dependency, no new build tooling — the same single-file SPA and the same
publishable-key + user-JWT path to PostgREST.

Every figure comes from a server row, and each read names its columns:

- **Home** — greeting and avatar from the verified `farmers` row, Active produce
  from `listings` (`quantity_kg − reserved_qty` + `stock_status`), New orders and
  Needs action from `orders.status` filtered through the real `set_order_status`
  transitions, Awaiting payout / Paid out from `payouts` only. The fabricated
  `24°C` weather tile, the all-orders "Income" figure and the all-listings
  "Active" figure were removed from the farmer home, together with the
  `samplePrices()` fallback.
- **My Produce** — the real stock model (Live / Low stock / Fully reserved /
  Hidden from buyers / Temporarily unavailable) each with the next step, only the
  farmer's own photographs (`image_url`/`photo_url`, never `listingImage()`'s
  stock photo), tab counts from the same rows.
- **Orders** — reference (`tracking_code` else order id), crop + quantity from
  `notes`/`item_category`, date, total using the ledger's own precedence
  (`total_amount` → `total_price` → qty×price), payment status, method, and only
  the transitions the server allows. Cancellation asks first, because it is the
  one action that cannot be undone from this screen.
- **Earnings** — `payouts` records only: gross, commission and net as stored,
  bucketed by `services/finance.py`'s status sets. An order is never presented as
  money. Transport is stated as not recorded rather than invented.
- **Profile** — name, phone and district editable through
  `POST /api/user/profile` (server validates, normalises `+256`, 409s on a
  duplicate); verification, premium, sub-county, village, farm size and crops are
  read-only; the avatar goes through `POST /api/uploads` then an owner-scoped
  `profile_photo` update.

`sb()` returns `null` on a failed read and `[]` on a genuinely empty one, and
that distinction is carried to every screen: an outage reads "Unavailable — try
again" with a retry, never "you have nothing".

**Two real defects found and fixed on the way** (both pinned by tests):

1. `attachListingMedia()` had been deleted by the buyer-redesign commit `74879f8`
   while both call sites survived, so choosing a photo in **Sell produce** threw
   `ReferenceError: attachListingMedia is not defined` and silently dropped the
   photograph. Restored from `ce64899`; the listing still saves first, so a slow
   upload can never cost a farmer the listing.
2. A double tap on **Post Listing Free** posted the same crop twice, because
   nothing held the in-flight POST. Added a save lock plus visible "Saving your
   listing…" progress. With the lock disabled the new test sees two identical
   POSTs; with it enabled, one.

Also fixed: the verified DB name/photo arriving after the home had painted left a
stale greeting (`fhHeadRefresh()`), and the section links were click-only anchors
no keyboard could reach (now real buttons). The buyer's phone number is now shown
only once the farmer has accepted an order, matching the accepted privacy policy
v1.0.0 §"Buyer and seller" ("private from each other until an order requires it")
rather than on every open order.

**A visual pass over all five screens at 390×844 found what no assertion had
caught.** The farmer Home was 4677 px tall: `applyHomeMode()` un-hid every
non-panel child of the home page, so a seller's dashboard was followed by the
entire buyer storefront — search, hero, category chips, two 720 px product grids
and the buyer footer. It now hides everything except the dashboard and the
footer's contact/legal block, and the page measures 2438 px; a regression test
asserts those five storefront sections compute `display:none` on a seller's Home.
The same pass set the remaining touch targets to the 44 px floor the mandate
asks for — the produce/orders filter chips (`.fh-tab`, was 40 px) and the buyer
contact link, which moved off the phone-number line into a 44 px
"Open delivery & tracking" button (was a 32 px inline link). Dead code
(`fhViewListing()`, defined and referenced nowhere) was deleted rather than left
in. Both CSS rules were checked by reverting them and watching the new
assertions fail (32 px, and the chip row), then restoring.

**Verification:** 12 new Playwright tests over the real HTML with deterministic
fixtures (own rows vs another farmer's, legal-transition buttons, payout-derived
money, exact `POST /api/user/profile` body, empty-vs-error states, the seller
Home suppressing the buyer storefront, 44 px filter chips and contact button,
zero page errors). Both new defect tests were confirmed to fail against the
broken code, as were the two touch-target assertions against the reverted CSS.
`tests/e2e/test_farmer_ui.py` 12 passed (23 s). All 40 browser tests pass
(79.4 s). Full suite **459 passed** (85.2 s). `ruff check app.py services/
scripts/ tests/` clean on local 0.16.10. All 6 inline script blocks parse.
Heights are asserted with 0.1 px slack because Chromium lays a `min-height:44px`
chip out at 43.99998 CSS px; the same probe measured every chip and the contact
button at exactly 44.0 px.

**Production status: DEPLOYED AND VERIFIED LIVE (2026-10-23).** The owner
authorised completing and pushing the farmer interface, so it is no longer
working-tree only. See §Stage F2 for the commit, CI and live-comparison evidence
— it is the same bundle, published together, because both stages live in one
`static/index.html`.

What *could* be checked live without a deploy was checked: a read-only probe
against the real Supabase project sent the four select strings scraped from
`static/index.html` (61 columns across `listings`, `orders`, `payouts` and
`farmers`) using only the publishable key the shipped SPA already carries. All
four returned `200` with no "column does not exist" error, and `orders`/`payouts`
returned zero rows to the anonymous role — so the schema the screens name is
real and RLS still hides a farmer's money from anon. What that does **not**
prove is the authenticated path: no real farmer sign-in was performed, so the
numbers themselves are still proven only against fixture rows of verified shape.

**Deferred, with the reason:**

- **Listing edit / pause / quantity change** — closed by Stage F2. F1 shipped
  with the produce screen saying plainly that editing was unavailable rather than
  showing dead Edit buttons; `PATCH /api/listings/<id>` now provides the
  business-rule-safe path those buttons needed.
- **Photo preview / replace / remove** — closed by Stage F2 (`#lpPhoto` is no
  longer a bare file input).
- **`orders_farmer_update` allows a farmer to write any column of their orders**
  (`supabase/base-schema.sql`, no `with check` column restriction). Pre-existing
  security defect; the fix is a migration and none is authorised here. The
  farmer UI only ever calls `POST /api/orders/<id>/status`.
- **Transport** — the app records no transporter charge or delivery fee, so no
  screen claims a matching, tracking or cost it cannot back.

---

## Stage F2 — Managing a listing (2026-10-23) — DEPLOYED AND VERIFIED LIVE

Closes both F1 deferrals: a farmer can change a live listing, and can see,
replace or remove a photograph before posting one.

**The guarded write.** `PATCH /api/listings/<id>` re-reads the row filtered on
`id` **and** `farmer_id`, refuses a `quantity_kg` below that row's
`reserved_qty` (409 carrying the reason and the real figure), re-derives
`stock_status` with the reservation triggers' own thresholds (unavailable when
remaining ≤ 0, low_stock at ≤ 10% of quantity), and writes through
`SupabaseClient.update_checked`, which compare-and-swaps on the `reserved_qty` it
just read: zero rows back means an order landed first, so the edit is rejected
and the farmer is told to reload instead of the write silently overwriting a
reservation. PostgREST cannot compare two columns inside a filter, which is why
the guard is an exact match on the observed value rather than a
`reserved_qty=lt.quantity_kg` expression.

No migration was needed or made: `listings_owner_update` already lets an owner
write any column, so the browser could always have done this. The point of the
endpoint is that the stock rule is now enforced server-side instead of trusted
from the client.

**The forms.** `Sell produce` gained a real photo picker (preview, replace,
remove, type and size validation before any byte leaves the phone), byte-level
upload progress from XHR instead of a decorative bar, an unsent-draft restore
limited to the farmer's own typed fields (no session or payment data in
localStorage), and a one-save lock. Every My Produce row now carries
`Edit listing` and `Hide from buyers` / `Show to buyers`; the edit sheet shows
what buyers can still order against what is reserved, prices per-unit and total
from the discounted figure buyers actually pay, and on a 409 adopts the server's
reserved number rather than leaving the farmer to guess.

**Two honesty bugs found while testing, both fixed:**

- A failed photo upload left the produce row reading "📷 Adding photo… 95%"
  indefinitely while the toast said the photo had failed. The row now reads
  "📷 Photo not added — open Edit listing to try again".
- Clearing an `<input type=file>` programmatically fires a real `change` event,
  so the media reset after a successful post re-saved the just-published details
  as an "unsent draft" — which would have put them back on the next open and
  invited a duplicate listing. Draft saving is now limited to while the form is
  actually open.

**Verification.** `tests/test_listing_edit.py` 14 tests (unauthenticated 401,
not-owned 404, ownership filter on both read and write, quantity below the
reserved floor refused with no write, `stock_status` thresholds, the in-flight
reservation losing the race and returning 409, bounds, `is_available` touching no
stock, transport-failure 502). `tests/e2e/test_farmer_ui.py` 21 tests, 9 of them
F2, driving the real `static/index.html` in headless Chromium. Full suite
**482 passed / 0 failed**; `ruff check app.py services/ scripts/ tests/` clean;
all 7 inline `<script>` blocks parse.

Seven deliberate mutations were made to the shipped code, one at a time, each
reverted and byte-compared afterwards, to prove the new tests are not decorative:
dropping the local reserved-floor check, dropping the one-save lock, dropping the
409 reserved-figure adoption, making hide/show write an extra column, reverting
the failed-upload pill, ignoring the discount in the total line, and removing the
draft-open guard. **All seven were detected by the matching test.**

**Working note for the owner.** At 08:41 and again at 09:09 this dashboard work
was removed from the working tree by a `git stash` + reset issued by another
session sharing the repo (both stashes are still listed by `git stash list`;
nothing was dropped). It was recovered with `git stash apply` onto the newer HEAD
that session had pushed (`ff107ec`, a 2-line navbar change), and the merge was
checked to keep both changesets — the i18n helpers and the farmer dashboard are
both present, and the navbar edit is untouched. The work is now committed, so a
later `git stash` cannot take it again.

**Production status: DEPLOYED AND VERIFIED LIVE (2026-10-23).** The owner's
instruction was to finish the work, push it, and have it reflect in production.
`a02829a` (F1) and `d5bd6c0` (F2) were rebased onto the other session's
`365acec` and pushed to `main`; `git rev-parse HEAD origin/main` now returns the
same sha for both and the working tree is clean.

* **CI** — on `d5bd6c0`, *Backend lint + type + tests* and *Migration 0007 on
  real PostgreSQL (PGlite)* are both `completed / success` (run `39992212374`),
  and the *Deploy public web app* workflow's `deploy` job (`39992212165`)
  succeeded. The suite that passed locally under ruff 0.16.10 therefore also
  passes CI's pinned 0.8.4.
* **Frontend** — the comparison that survives `core.autocrlf=true`:
  `git show HEAD:static/index.html` and `curl https://agribrige.com/` hash to the
  same `137949deee95a6e4a4f40589c0b35c76250a7ae93d6e220c0131995596931ce1` at
  858,366 bytes, and `https://agribridge.zealmugumya.workers.dev/` returns those
  identical bytes. The served HTML contains `listingEditModal`, `fhProduceRow`,
  `leReservedLine` and `lpSaveDraft`, so the edit sheet and the F1 screens are
  what a farmer's phone actually downloads — not merely what the repo holds.
  Reached as a guest, all four farmer screens render their "Log in…" lock state
  rather than any rows, so shipping the interface did not ship a way to read
  someone else's produce, orders or payouts.
* **Backend** — the guarded write is live on Render, proved by discriminating
  probes instead of by a `200`: `PATCH /api/listings/999999` with no token →
  `401 {"error":"Missing token"}`, and with a forged bearer →
  `401 {"error":"Invalid or expired session"}`, while a nonexistent path →
  `404 {"error":"Not found"}` and `GET /api/listings/999999` → `405`. The only
  `before_request` hook mints a correlation ID, so a `401` can only mean the rule
  matched and `verify_supabase_user()` ran. The route exists *only* from `a02829a`
  — `365acec:app.py` has no such rule — so `401` where the old build would answer
  `404` is the evidence that Render is running the new code. No row was written:
  both responses come from the auth check, before any database call.

**Verification round (2026-10-23, pushes `e8b512f` → `c34fa1d`).** Six commits,
all pushed and all published through the normal pipeline, with CI as the only
publisher. The first four went up in one push, so CI judged them at its tip:

| Push | Contents | CI |
|---|---|---|
| `e8b512f` | `static/_headers` + `tests/test_spa_headers.py` | judged at ↓ |
| `78fdad3` | 0018 + `farmer_ratings.test.mjs` | judged at ↓ |
| `a661ea0` | 0019 + `order_write_lockdown.test.mjs` | judged at ↓ |
| `61b0da7` | consent-test race fix | backend **success**, sql **failure**, deploy **success** |
| `6e9ace0` | CI publishes which SQL test failed as an annotation | backend **success**, sql **success** |
| `c34fa1d` | `_headers` reshaped to what the platform applies | sql **success**, deploy **success**, backend **success** |

The `61b0da7` SQL failure did not reproduce: the same eight files pass locally,
pass in a fresh clone of the pushed commit, and pass with CI's LF line endings,
and the job then went green twice in a row on later commits. It was therefore a
runner-side flake, and `6e9ace0` exists so that the next one names its file
instead of leaving an unauthenticated reader with nothing (Action logs need a
signed-in account; check-run annotations do not).

Locally, the full suite ran four times across these commits: 489 passed,
489 passed, 1 failed / 487 passed, and green again — the two failures were
*different* tests in `tests/e2e/test_consent_ui.py`, and that file is 22/22 green
whenever it is run on its own or as `tests/e2e`. Same known condition as above,
under load.

**What production still has not proved.** None of the above is an *authenticated*
farmer request. No real sign-in was performed, so the live edit, pause,
photo-upload and 409-lost-race paths have each run only against a fixture row and
a fake Supabase, and the figures on the screens are still verified against rows of
verified shape rather than against the owner's real listings, orders or payouts.
That last check needs a farmer account and is the owner's to authorise.

### Found while verifying production, not while verifying tests

Opening the live site in a browser (guest, no sign-in, no write) proved the
deploy *and* turned up a console error no fixture could show, because every
browser test stubs `farmer_ratings` with a fake row:

```
Failed to load resource: 404
…/rest/v1/farmer_ratings?select=farmer_id,avg_rating,review_count
```

* The live project has **no `farmer_ratings` table** (`GET …/farmer_ratings` →
  `404`; `listings`, `farmers`, `orders`, `payouts`, `animal_listings` all `200`).
  It does have `reviews` (`200`, zero rows), which is what `supabase/base-schema.sql`
  defines. No migration in the repo ever creates `farmer_ratings`.
* Nothing in `app.py` or `services/`, and nothing in the SPA, writes ratings —
  there is no review-submission route and no review UI. So the table is not
  missing by accident: the ratings feature has only ever had a read path.
* **The buyer home page degrades honestly.** `loadHomeStats()` guards on
  `Array.isArray(ratings)`, `window.farmerRatings` stays undefined, and
  `renderHomeFarmers()` — which returns early unless a farmer has
  `avg_rating > 0` — hides both the strip and its heading. Confirmed in the live
  DOM: `ratingsGlobal: undefined`, strip and heading `display: none`, and the
  page still rendered 6 real listing cards. No invented stars.
* `0004a_farmer_matching.up.sql:108` has the same dangling reference inside
  `match_farmers`. Its sibling tables (`purchase_requests`, `order_offers`,
  `matching_config`) do exist live, so that migration was applied — but the
  function would fail the moment anything called it. Nothing does: `app.py` never
  invokes `match_farmers`, so the automated-matching work (task #8) is
  code-complete and unusable against the live schema.

**Closed by 0018 — written, tested, awaiting the owner's paste.** Fabricating a
rating to make the strip appear is exactly what the mandate forbids, and pointing
the SPA at `reviews` would not have helped: that table is empty live. 0018
therefore creates only the aggregate the SPA already names —
`public.farmer_ratings` over `reviews`, `security_invoker` so the base table's own
RLS still decides who may see a review — and stops there. No rating appears until
a real review exists, and the strip stays hidden until then. The product question
of who rates what, and where the form lives, is still open; only the 404 is
resolved. `tests/sql/farmer_ratings.test.mjs` holds the line with 9 assertions,
starting from the pre-migration 404 and including "anon gets nothing once reviews
stop being public", which is why the view is not a bypass.

Reading `base-schema.sql:264` while verifying that schema turned up a second,
worse gap that no browser could show. `orders_farmer_update` lets a farmer UPDATE
their own order with no column restriction — RLS policies are row predicates, not
column grants — and 0016 puts an `after insert or update of status,
payment_status` trigger on `orders` whose function is `security definer`. So one
PATCH `{"status":"delivered","payment_status":"paid"}` from a farmer mints a
**pending payout they were never owed**, because the trigger inserts past
`payouts`' own RLS. Settlement later refuses to disburse it without a ledger
receipt, but the false obligation is already in the table the admin Finance view
totals. `payment_ref` and `notes` are writable the same way. Reproduced in a
PGlite sandbox with the real trigger shape, then closed by 0019 (`drop policy`),
because nothing in the app writes orders directly: the SPA has no write to
`/rest/v1/orders` and admin status changes go through
`/api/admin/order/<id>/status`, which runs as service role and still fires the
payout trigger. 12 assertions in `tests/sql/order_write_lockdown.test.mjs`,
including the service-role path still working after the drop.

A second console line pointed at a gap in the earlier audit, which had only
looked at API responses (`docs/PRODUCTION_AUDIT.md` §177): **the site's own
document is served with no security headers at all.**

```
curl -sI https://agribrige.com/  →  200, Server: cloudflare, CF-Cache-Status: HIT
                                     no Content-Security-Policy
                                     no X-Frame-Options
                                     no X-Content-Type-Options
                                     no Strict-Transport-Security
                                     no Referrer-Policy
```

The SPA carries all of these as `<meta http-equiv=…>` (line 5-8 of
`static/index.html`). A meta CSP is honoured by browsers, so the script/style
restrictions in it do apply — but `X-Frame-Options` is **ignored in a meta tag**
(the browser said exactly that in the console), and meta cannot express
`frame-ancestors` either, because that directive is disallowed in meta-delivered
policies. So the page that takes orders and payment details was frameable, in
spite of declaring itself not to be.

**Applied, and measuring it changed the design.** `static/_headers` now ships the
four universal headers on `/*`, published by CI on `c34fa1d` and verified on the
response:

```
curl -sSI https://agribrige.com/  →  200, CF-Cache-Status: HIT
  x-content-type-options: nosniff
  x-frame-options: SAMEORIGIN
  referrer-policy: strict-origin-when-cross-origin
  Strict-Transport-Security: max-age=15552000; includeSubDomains
```

The same four arrive on `/admin.html`, `/logistics.html` and
`agribridge.zealmugumya.workers.dev`, none of which declared any policy before.
`SAMEORIGIN` is what the meta already claimed to set — not a stricter policy
than the site believed it was running — and no legitimate embedding of the site
is known or used, so nothing depended on the hole.

**The CSP header was tried and removed, on evidence.** The first version of the
file also declared a CSP for `/` and `/index.html`. CI reported `deploy`
*success*, wrangler said `Uploaded 2 worker bundle files`, and the served
document returned the four universal headers with **no `Content-Security-Policy`
at all** — on both domains, both `HIT`. A path-scoped `_headers` block is not
honoured by this assets runtime, and the only alternative is `/*`, which would
have bound a policy on `admin.html`, `logistics.html` and `transporter.html`:
three screens that declare no CSP today, have never been measured against one,
and are the ones the owner runs the business from. Their inline handlers survive
(`script-src 'unsafe-inline'`), but a `frame-src` limited to YouTube would have
been an untested guess about any map embed they use. So the file ships the
headers that meta cannot deliver, and `index.html` keeps its meta CSP, which
browsers honour and which the live reload re-confirms: `[AgriBridge Smoke Test]
11/11 passed`, with the pre-existing `static.cloudflareinsights.com` block (Cloudflare's
own analytics beacon, refused by the policy the page has always carried) and the
`X-Frame-Options may only be set via an HTTP header` line that this change now
makes true — the meta stays in the file for the Capacitor shell, which loads the
HTML without going through the Worker.

Rollback is `git revert` of `c34fa1d`; CI republishes.

**Third finding from the same reload, left for the owner.** Six
`images.unsplash.com` requests failed in the console, which led to the code and
then to the data. `listingImage()` (`static/index.html:3639`) falls back to
`cropPhotoUrl()` (`:3633`, a table of 19 Unsplash crop ids) whenever a listing has
no `image_url`. A read-only probe of the live project says **all 63 `listings`
rows have an empty `photo_url`**, so every buyer card and product detail on the
site is currently showing a stock photograph over a real farmer's listing, with
nothing on screen saying it is illustrative. The farmer screens already refuse
this (`:7102` — "Never a stock photograph: only the farmer's own uploaded image,
else an emoji"), so the buyer side is the inconsistent one. Not patched here:
removing the fallback would leave the owner's redesigned home grid showing 63
blank tiles, and adding an "Illustrative photo" label changes a screen they
designed. Either is their call, and the record is what matters — the mandate
forbids presenting stock photographs as a farmer's actual harvest, and today's
production site does exactly that for every listing.

---

## 2026-10-27 — The mobile design was not regressed, production was overwritten

The owner reported six visual defects on the mobile homepage: bottom nav wider
than the screen, a low-contrast hero, a cookie panel covering too much, a
crowded header, nav items cut off, and an unpolished overall presentation. The
brief was to recover the previous design from Git history without redesigning.

**There is no CSS regression in this repository, and no commit here caused it.**
`agribrige.com` is currently serving a different application: a July-2026
snapshot of a *different repository* on the same machine.

### The evidence

| Check | Result |
|---|---|
| `curl -o` + hash of `https://agribrige.com/` | **410,311 bytes**, normalised sha256 `a01d2ac6…faa213` |
| Same hash across all 329 commits of this repo (`git log --all`, `static/index.html`) | **no match** — production is serving content that was never committed here |
| Current `origin/main` (`ef9ea16`) `static/index.html` | 872,198 bytes — production is serving **less than half** the real bundle |
| `Deploy public web app` on `ef9ea16` | run `40448483582`, **success**, 2026-10-27T04:12:48Z |
| `agribridge.zealmugumya.workers.dev/` (origin, no site cache) | identical 410,311 bytes / `a01d2ac6…` — the Worker itself holds the wrong asset, not a stale CDN entry |
| File on disk with that exact size and hash | `C:\Users\HP\agribridge-app\static\index.html`, mtime 2026-10-27 **07:18** — six minutes *after* the good deploy |

`C:\Users\HP\agribridge-app` is **not a clone of this repository.** It is
`github.com/zealmugumya-creator/agribridge-app` — 34 commits, a different root
commit (`1e5bc0e` vs this repo's `e778409`), `app.py` of 29 KB, Capacitor and
Gradle scaffolding, and a `static/index.html` whose last real change was
2026-07-20 (`ff3b8a7`). On 2026-10-26 21:04 a hand-written `wrangler.jsonc`
appeared in it naming **the same Worker**:

```json
{ "name": "agribridge", "compatibility_date": "2026-10-06", "assets": { "directory": "./static" } }
```

`wrangler deploy` replaces the whole asset set, so that one command replaced the
production Worker's files with that directory's two files. Verified gone from
production as of this writing (all `404`):

`/sw.js` · `/manifest.json` · `/robots.txt` · `/sitemap.xml` ·
`/business-config.js` · `/icon-192.png` · `/logistics.html` ·
`/transporter.html` · `/legal/privacy.html`

The four response headers shipped on 2026-10-23 by `static/_headers` (`e8b512f` →
`c34fa1d`) are **absent again** — that directory has no `_headers` file, so the
`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and
`Strict-Transport-Security` values no longer exist on the Worker. `curl -sSI
https://agribrige.com/` returns only Cloudflare's own headers (`Date`,
`Content-Type`, `CF-Cache-Status`, `Cache-Control`, `Report-To`, `Nel`, `Server`,
`CF-RAY`, `alt-svc`).

### Every reported symptom reproduces in the stale build, and only there

Measured with Playwright (Chrome channel, Android UA) against both files served
locally, so the comparison is of markup and CSS, not of a moving production
target. `C:\Users\HP\diag\measure.py`, raw output in `diag/bad.json` and
`diag/restore.json`.

| Symptom | Live stale build | This repo `ef9ea16` |
|---|---|---|
| Bottom nav beyond the screen | 17 items, **791 px of content in a 360 px bar**, `overflow-x:auto` → scrollable | 4 items + centre orb + More, **306 px in 360 px**, not scrollable |
| Nav items cut off | **9 off-screen at 360 px and 390 px** (AI Doc, Supply, Community, About, Contact, B2B, Finance, Cold, Export); 4 at 768 px | **0 at every width** (360/390/768/1440) |
| Cookie panel obscuring the page | `style="display:none;…;display:flex"` — a duplicate property, so the later one wins: **visible from first paint**, `pointer-events:auto`, over the hero CTAs | hidden at load, revealed at 3 s with `pointer-events:none` on the container and `auto` on the two buttons |
| Hero contrast | white `h1`, `text-shadow:none`, overlay only `rgba(6,15,9,.35)→.72` with the title at 18 % down the block | **no element uses `.hero` at all** — the home page renders `.bh-hero`, a solid emerald card with `#fff` title and `#a8f5c0` accent |
| Crowded header | no wordmark at 360 px, just two bare icons + `Join Free` | `AgriBridge` wordmark + bell, `Log In` / `Join Free`, search row below |
| Horizontal overflow | none (`scrollWidth == innerWidth`) | none (`scrollWidth == innerWidth`) |
| Console | **`pageerror: Unexpected identifier 'market'` at every width** — the stale bundle's script does not parse | no page errors |

Note the consequence for the two "hero fixes" pushed today: `ef9ea16` here and
`84f775e` in `agribridge-app` both edit `.hero`, and **`grep -c 'class="hero'`
returns 0 in both files.** Both changes are to dead CSS. Neither can have
improved what the owner saw, and the `agribridge-app` one was followed six
minutes later by the deploy that caused the report.

### Restoration, and why nothing was changed to achieve it

The last known good design is this repository's `origin/main` (`ef9ea16`) — the
bundle whose Stage 1 + F1/F2 production smoke test passed on 2026-10-23. It
needs no repair for the six symptoms, so the recovery is a **republish, not a
code change**: re-run `Deploy public web app` on `ef9ea16`. Doing it through the
workflow (not from a workstation) keeps the one audited publisher and uses the
CI-scoped Cloudflare token.

Not done here, and deliberately so: no `git revert`, no branch reset, no
`static/` edit, no migration touched, and no attempt to "improve" the layout
while the owner is still looking at the wrong app. The one genuine polish
candidate found in the correct bundle, that the consent banner occupies ~20 % of a
360 × 740 viewport when it appears (147 px tall, both buttons on screen,
`pointer-events:none` on the container so it never blocks a tap behind it, and
`ab_cookies='1'` confirmed written on Accept) is noted here, not patched, because
it is a judgement about a screen the owner designed and it is unrelated to the
regression being recovered from.

**Prevention, pending the owner's decision.** Two things make this repeatable:
`agribridge-app` holds a `wrangler.jsonc` that names this Worker, and nothing in
this repository detects a publish that shipped the wrong content. The first is
one `mv` in a directory that is not part of this workspace, so it is the owner's
to run. The second would be a post-deploy check that the live hash equals the
committed blob — cheap, and it would have caught this within a minute.

---

## 2026-10-27 — Admin console: measured baseline (before any change)

Every number below was produced by an actual request or an actual browser layout
run. Nothing is estimated. Two harnesses live outside the repo:
`C:\Users\HP\diag\admin_baseline.py` (production `/admin`, unauthenticated) and
`C:\Users\HP\diag\admin_layout.py` (the real `static/admin.html` code paths driven
against local fixtures, so the authenticated layout is measurable without the
admin password).

**What could not be measured and why:** there is no local `.env` and no admin
password in this workspace, so no request reached an *authenticated* endpoint.
Per-request figures for `/api/admin/stats` and the list endpoints are therefore
decomposed from the two measurable halves — Render's HTTP floor and Supabase's
query time — and labelled as such below. The dashboard's own new Performance panel
(task #67) exists to produce the authenticated numbers on the owner's screen.

### Where the time actually goes

| Measurement | Result | How |
|---|---|---|
| `https://agribrige.com/admin` load event | **1,062–1,277 ms** at 320/360/390/430/768/1440 px | Chrome navigation timing |
| Login form interactable (`#pwd` focusable) | **1,134–1,415 ms** | same |
| Requests for the login screen | **2** (the document + Cloudflare `beacon.min.js`) | resource entries |
| Console errors on `/admin` | **none** | console + pageerror listeners |
| `GET /health` on Render, warm | **1.14–1.28 s** (connect 0.07 s — TTFB is the whole cost) | curl, 5 samples |
| `GET /api/admin/verify` (401, no DB touch) | **0.60 s** ×3 | curl |
| `POST /api/admin/login` wrong password | **0.62 s**, `401`, no session created | curl, one call |
| PostgREST read, `select=id&limit=1000` | **0.29–0.51 s** per table | direct probe, publishable key |

So the login *screen* is not slow. The cost is per API request: **~0.6 s of Render
round trip before any query runs**, plus ~0.3–0.5 s per Supabase read.

### Structural causes found in source

1. `render.yaml:7` starts **`gunicorn --workers 2`** with sync workers, while
   `refreshAll()` (`admin.html:287`) fires **13 requests** on every login and every
   Refresh — measured as 14 fetches through the fixture harness. Two sync workers
   serialise that fan-out, so "parallel" in the browser is sequential on the server.
2. `refreshAll()` `await`s `loadStats()` **before** starting the 12 list loads, so the
   slowest endpoint gates the whole dashboard.
3. `/api/admin/stats` (`app.py:1405`) downloads **five tables at `limit=1000` with
   every column** (`farmers`, `listings`, `orders`, `animal_listings`, `transporters`)
   and then only calls `len()` on them — five sequential Supabase round trips to
   count rows. PostgREST answers the same question with `limit=0` +
   `Prefer: count=exact`, which returns `Content-Range: */84` and an empty body
   (verified against the live project).
4. `loadStats()` ends with `catch(e){}` — a failed KPI query leaves the grid **empty
   with no message**, indistinguishable from zero.
5. `load()` retries once after a blind **1200 ms** wait for *any* error, including a
   cold start that needs far longer, and `api()` has **no timeout and no abort**, so a
   stalled request spins forever.
6. `showTab()` (`admin.html:645`) calls `load(t)` on **every click**, refetching data
   already in `DATA`.
7. `match()` (`admin.html:647`) runs `JSON.stringify(row)` per row **per keystroke**.
8. `doLogin()` has **no in-flight guard** — the button stays live, so a slow
   password entry allows duplicate submissions against a 5-per-300 s rate limit
   (`app.py:589`).
9. `logout()` clears the token but **not `DATA` or the finance cache**, so the next
   admin on a shared machine sees the previous session's rows until they reload.
10. `SUPA_URL`/`SUPA_KEY` (`admin.html:167-168`) are declared and **never used** —
    dead code that reads like a credential leak.

### Layout causes, measured on the real code at 360 × 740

| Symptom | Measured |
|---|---|
| Tab bar | **19 nodes, 1,841 px of content in a 320 px bar → 8 wrapped rows, 347 px tall** (392 px / 9 rows at 320 px) |
| KPI block | 6 cards, **333 px tall**, number font 25.6 px, revenue value wraps to two lines |
| First actionable row | starts at **≈1,750 px** down; total page height **3,416 px** (≈4.6 screens) |
| Data table | forced `min-width:640px` (`admin.html:53`) → renders 640 px wide in a 360 px viewport, header clipped mid-word; scroll is contained in `.tblwrap`, so the *page* does not overflow |
| Header | 116 px tall at 320/360 px (wraps to 2 rows) |
| Overview chart | 150 px tall, 7 slots; six months of genuine zeros beside one bar — honest but wasteful |

### RLS / authorization posture (relevant to any caching decision)

`render.yaml:18-21` documents `SUPABASE_KEY` as the **service-role** key, so every
`supa_get()` read bypasses RLS and admin authorization rests entirely on the Flask
JWT role check — not on a policy. Anonymous probes confirm the tables are locked
down for the publishable key: `orders`, `deliveries`, `purchase_requests`,
`order_offers`, `service_requests` return `Content-Range: */0`, and `transporters`
and `system_events` return `42501 permission denied`. Consequence for this task:
**no authenticated admin response may ever be cached publicly**, and no widget may
be fixed by widening a policy.

## 2026-10-27 — Admin console: redesign and repair (after)

`static/admin.html` was rewritten in place: **+839 / −230 lines, 72,785 → 113,656
bytes**, one inline `<script>` (83,055 bytes of JS), still no framework and no CDN
library. `app.py` was changed in the earlier commit `a40408c` (counting stats
instead of downloading tables). No other file was touched, no route or table was
added, and no feature was removed: the console still reaches the same 18
destinations plus the logistics board.

### How to read the two kinds of number in this section

| Kind | What it is | Where it came from |
|---|---|---|
| **Production measurement** | A real request to a live server, or a real load of the live page | `admin_baseline.py`, curl, 5 samples — recorded in the baseline table above |
| **Browser layout measurement** | Real Chrome laying out the real `admin.html` code paths, against fixture rows | `admin_layout2.py` — the pixel and count figures below are these |
| **Service model** | **Not a measurement.** Each API reply is held for 600 ms and only 2 are in flight, from two *measured* facts: Render's warm floor (~0.6 s/request) and `gunicorn --workers 2` | `LATENCY_MS`/`CONCURRENCY` in `admin_layout2.py` |

The fixture rows are invented so the CSS has something to lay out. **Nothing in
this section is a business figure.** Both builds get byte-identical fixtures, on
the same machine, back to back, so the only thing the numbers can express is how
each build spends the same service capacity.

### Timing, same model, both builds, idle machine

Both builds, back to back on an idle machine, fixtures byte-identical, same
600 ms / 2-slot service model, no CPU throttling. Each range is the six widths
(320 · 360 · 390 · 430 · 768 · 1440) of one run — `diag/old_run.txt` and
`diag/new_run.txt`, from `admin_layout2.py`.

| Stage the brief asked for | Old console | New console | What the number is made of |
|---|---|---|---|
| **Initial page load** (document + CSS + first paint) | 131–253 ms | **102–134 ms** | Pure browser time, no network. The new file is 39 KB *larger* and still parses faster, because the sign-in screen's own layout went from 19 wrapped nav tabs (393 px of them) to a 57 px bar |
| **Login form interactive** | 0 ms after load | 0 ms after load | neither build disables the password box before submit |
| **Login request → dashboard shell** | 742–889 ms | **704–748 ms** | one modelled round trip (600 ms) + the JS that builds the shell. Production anchor for the same hop: `POST /api/admin/login` answered in **0.62 s** (wrong password, no session) |
| **Role / permission resolution** | one `/api/admin/verify`, same 600 ms floor | **unchanged** | the shell is rendered from the login reply; `verify` runs only on a *returning* session. Production anchor: `GET /api/admin/verify` = **0.60 s** ×3 (that path touches no database) |
| **Time to first real KPI digit** | 1,240–1,261 ms | 1,270–1,296 ms | **did not improve, and that is honest.** Both builds need login → then metrics: two sequential round trips, ~1.2 s of the model's floor. Nothing in a redesign removes a server hop that has to happen before a number is true |
| **Time until the first screen has real data** | 1,830–1,848 ms | **1,239–1,257 ms** | latest of login / metrics / order rows. **−595 ms, −32 %** — metrics and the first list now leave together instead of the lists waiting behind the metrics. This is the one headline that reproduced across all three back-to-back pairs |
| Replies **served** by the time the first KPI digit appears | 2 (login, metrics) | 3 (login, metrics, **order rows**) | the chart and the section tables already have their data when the numbers appear, instead of 600 ms later |
| Requests *issued* by then | 14 | 3–14 depending on width | **not a win, stated plainly:** both builds fan all 14 out inside 1.5 s. What changed is the order they are served in, not how many are queued |
| **Time to complete dashboard loading** | 4,838–4,855 ms | 4,879–4,900 ms | **+45 ms, deliberately.** The 11 list endpoints are prefetched for the badge counts and instant tab switches; the overview no longer waits for them |
| **Number of network requests** | 14 | 14 | unchanged. Fewer requests was never achievable: 12 sections × 1 endpoint each is the floor for instant tab switching |
| Filter keystroke, 24 order rows | 3.9–11.1 ms | 4.4–11.9 ms | browser only, both under 12 ms on this fixture — dropping `JSON.stringify` neither won nor lost measurably |
| Console errors / 404s | clean | clean | both builds |

**What this does *not* say.** It does not say the new console loads faster in
production. Under the model, the first screen is 32 % sooner and the whole board
lands at the same time; the only production figures here are the unauthenticated
ones in the baseline table above, and the authenticated endpoints stay unmeasured
until the owner opens the console and reads its own Performance view.

### Throttled mobile: what it measured and what it cannot

The brief asks for a throttled run "where tooling permits". Chrome's
`Emulation.setCPUThrottlingRate` at **4×** was applied to both builds at
390 × 844 (same fixtures, same service model) — `THROTTLE_X=4` in
`admin_layout2.py`, artifacts `old_thr.txt` / `new_thr.txt` / `*_cost*.json`.

**The wall-clock half of it is not usable.** Re-running the *same* build three
times under 4× throttle gave login→shell of 3,795 / 4,112 / 3,749 ms and document
load of 1,055 / 1,328 / 1,434 ms — a spread of about ±300 ms against differences
small enough that the run order could explain them. Throttling stretches the
harness's own polling and the fixture's timers along with the page, so a throttled
before/after table would be reporting noise. It is recorded as a limitation, not
as a result.

**The attributed half is stable, and it is what the throttled run was for.** Summing
the self-time the stopwatch collected over the login path:

| | Old console | New console, as first written | New console, after the fix |
|---|---|---|---|
| Main-thread self time, login → every panel painted (4× CPU) | ≈ 320 ms | ≈ 2,960 ms | **≈ 1,960 ms** |
| `renderOverview` calls for one login | — (it paints once) | 15 | **6** |

The residue is one-time and honest: `showDash` 546 ms and `showTab` 536 ms at 4×
(≈ 270 ms of a 1× budget) exist because the Overview is now a real command centre —
18 labelled destinations, four KPI cards, a chart, 14 secondary metrics with their
definitions and an activity list — where the old screen painted six numbers. It is
paid once per sign-in, not once per click. Removing that work would mean removing
the dashboard, so it stays.

### What is now true on a phone

| Reported symptom | Old, measured at 360 × 740 | New, same fixture, same probe |
|---|---|---|
| Navigation chrome | 19 tabs, 1,915 px of labels in a 320 px bar → **9 wrapped rows, 393 px tall** | **57 px top bar + 61 px bottom nav**; the rest is an off-canvas drawer that measures `visibility:hidden`, `left:-238px` when closed |
| KPI cards | 6 cards, **333 px** of block, 25.6 px numbers, revenue wrapping | 4 cards, **152 px**, 18.56 px numbers; 14 secondary metrics listed underneath with their source |
| Monthly orders chart | `#overviewExtras` block **806 px** tall, 3 children, **clipped at 320 px** | `.chart` **92 px**, 6 month bars, `clipped:false` at every width |
| Where the first usable control sits | `#search` at **y = 1,737 px** — below four screens | `#search` at **y = 83 px** on all 18 sections |
| Page height, overview | 3,480 px | 1,321 px |
| Page height, per section | 1,910–3,480 px | 740–1,712 px |
| Data table | forced `min-width:640px` → 640 px wide in a 360 px viewport | natural width 441–707 px; scrolls **inside its own wrapper** with the first column `position:sticky` (`.tblwrap.scrolling`), page `scrollWidth` still 360 |
| Elements crossing the viewport | **6** (table, thead, tr, 3 th) | **0** at every width 320–1440 |
| Layout viewport on a 320 px phone | **331 px** — the old CSS forced the visual viewport wider than the screen | **320 px** |
| Console / failed requests | clean | clean, and **no 404s**: every path the console calls exists in `app.py` |

Per section at 360 × 740 (old → new): page height orders 3,444 → 1,712, listings
3,480 → 956, animals 2,312 → 740, supplies 2,538 → 846, farmers 2,606 → 940,
transporters 2,229 → 740, deliveries 2,312 → 740, requests 2,143 → 740, offers
2,255 → 740, cold storage / refrigerated transport / loans 2,120 → 740, matching
1,910 → 740, inbox / scans 2,038 / 1,910 → 740, activity / errors 1,910 → 740,
finance 2,012 → 740. Fourteen of the eighteen data sections now fit one screen
exactly (740 px = the viewport height, i.e. no page scroll at all); orders,
listings, farmers and supplies still scroll because they legitimately hold more
rows, and
they are the four that got sticky first columns and per-table scroll wrappers.
Table width went from the forced 640 px to a natural 441–707 px; the
four genuinely wide tables (transporters 707, loans 705, cold storage and
refrigerated transport 642) scroll inside their own wrapper, which is the intended
behaviour, not a page overflow.

### Four things only a screenshot caught

None of these failed a probe — the layout numbers were clean while all four were
present. They were found by looking at rendered pixels, which is why
`diag/admin_shots.py` exists now (it captures real 360 × 740 **viewports**, not a
stitched full-page image: a full-page capture puts `position:fixed` elements in the
middle of the document, which made the bottom nav look like it was covering the
chart when it was not).

| What the phone showed | Cause | Now |
|---|---|---|
| "Loan requests **requests** awaiting a decision" | the alert label appended `" requests awaiting a decision"` to a tab title that already ends in "requests" | label is `title + ' awaiting a decision'`; `admin_func.py` now fails the run if any two adjacent words repeat |
| "METRICS AS OF **0S AGO**" | `ago()` had no case under 5 s, so a fresh read looked like a rounding error | `ago()` returns "just now" under 5 s and the header reads "updated just now" |
| Chart range labelled "**last 6**" | the `<small>` interpolated only the number | "last 6 months", matching the axis footnote the panel already carries |
| The orders table gave no sign it scrolls; the Status column was cut mid-word and the row's Approve/Reject buttons were simply not on the page | the "scroll the table sideways" hint was a sibling **below** the table, so on a long table it was off-screen | the hint is `position:sticky` at `bottom:var(--hintb)`, where `--hintb` is the bottom-nav height plus the safe-area inset on phones and `0` on desktop — visible in `diag/v4_orders.png` |

The last one is a limitation that remains, not a bug that was closed: on a 360 px
screen the actions column of a wide table is reached by scrolling, not by tapping.
The brief allows "careful horizontal scrolling inside individual data tables", the
first column stays pinned, and the hint now says so — but an admin who approves
orders from a phone should know the buttons are one swipe to the right.

### A live report that turned out not to be a performance problem

The owner sent a phone screenshot of the **deployed** admin page — the pre-redesign
bundle, still what production serves — showing
*"Could not reach the server. Wait ~30s (it may be waking up) and retry."* after
typing the password. The page was open at a `*.zealmugumya.workers.dev` address
rather than `https://agribrige.com`. Measured from this machine, same minute:

| Probe | Result |
|---|---|
| `GET /health` on the Render service, twice | **200 in 1.50 s and 1.23 s** — the API is awake, so "it may be waking up" was false |
| `OPTIONS /api/admin/login` with `Origin: https://…zealmugumya.workers.dev` | 200, but **no `Access-Control-Allow-Origin` header at all** |
| Same preflight with `Origin: https://agribrige.com` | 200 **with** `access-control-allow-origin: https://agribrige.com` |
| `GET /api/admin/stats` with the workers.dev origin | **401 with no ACAO** — the server answered; the browser was refused the answer |

`SETTINGS.cors_origins` ([services/config.py:69-79](../services/config.py)) allows
`agribrige.com`, `www.agribrige.com`, `PUBLIC_BASE_URL` and the Render origin. A
workers.dev preview host is not in that list, so Chrome blocked the login POST
before it left the phone, and `fetch()` rejected with a plain `TypeError` —
indistinguishable, in the old bundle, from a dead connection.

Two things follow. Operationally: **open the console at `https://agribrige.com/admin`,
not the workers.dev address** — widening the CORS allowlist to a shared preview
domain would let any project on that domain read admin responses, which is the
opposite of what the brief asks. In the code: the message blamed a sleeping server
for a blocked origin. The rewritten console now checks `location.hostname` against
the same list, says *"This page is open at X, which the API does not accept as a
source… Open https://agribrige.com/admin instead"*, keeps the cold-start wording for
genuine timeouts, and logs the real `err.name`/`err.message` to the console.
`admin_func.py` asserts both branches (115/115).

### Every structural cause from the baseline, and what happened to it

| # | Baseline finding | Now | Verified by |
|---|---|---|---|
| 1 | 13-request fan-out against 2 sync workers | unchanged in count, changed in **order and cost**: the first screen needs 3 replies; the other 11 are prefetched only once the browser is idle, for the badge counts | `admin_layout2.py` request log |
| 2 | `refreshAll()` awaited `loadStats()` before starting the lists | the metrics and the first list are issued together; the KPI grid paints when the metrics land, without waiting for the order rows | `first real KPI` vs `first-screen data complete` |
| 3 | `/api/admin/stats` downloaded 5 whole tables to `len()` them | replaced with `limit=0` + `Prefer: count=exact` counts, 10 of them through a 5-thread pool, in `a40408c` | 11 tests in `tests/test_admin_stats.py` |
| 4 | `loadStats()` ended in `catch(e){}` — a failed query looked like zero | a failed count stays `null` and renders "unavailable"; a metrics failure replaces the grid with a named error and a Retry; capped revenue says "at least" | `admin_func.py` state scenario |
| 5 | blind 1200 ms retry, no timeout, no abort | one retry after 1.5 s, then the panel reports the server's own message; every request carries a 20 s `AbortController`; `errText()` turns an abort into "the server did not answer within 20 s" | `admin_func.py` (asserts exactly 2 calls, not a loop) |
| 6 | `showTab()` refetched on every click | `ensure()` serves from `DATA` and only refetches after a failure; every **write** forces a fresh read of its own tab | `admin_func.py` write scenario |
| 7 | `JSON.stringify(row)` per row per keystroke | haystacks are built once per dataset; filtering is `indexOf` over strings | same fixture, same probe: 4.7–6.6 ms old → 5.2–8.7 ms new. Both under 10 ms, so this was never the bottleneck — reported as a wash, not a win |
| 8 | `doLogin()` had no in-flight guard | `AUTH_BUSY` plus a disabled button and field; two rapid submissions measured as **exactly one** POST; 401/429/503/abort each get their own sentence; the password box keeps what the admin typed | `admin_func.py` auth scenario |
| 9 | `logout()` cleared the token but not `DATA` | `logout()` clears `DATA`, `HAY`, `LOADING`, `DATA_ERR`, `STATS`, the finance cache and `PERF` | `admin_func.py` (asserts each is empty) |
| 10 | dead `SUPA_URL`/`SUPA_KEY` that read like a leak | removed; no `localStorage` is used by the console at all, and the only storage key is `ab_admin_token` | grep + the storage assertion |

### Three defects the rewrite introduced, caught by measurement

The first two were found by `admin_func.py`, the third only by a CPU-throttled
stopwatch. All three are fixed:

1. `renderActivity()` declared `var icon=icon(...)` inside the same function that
   calls the global `icon()` helper. `var` hoisting made the call hit `undefined`,
   so the **Activity tab was blank and threw a `pageerror`**. Renamed to `ic`.
2. `doLogin()` disables the password field and the button, and only re-enables
   them if the sign-in screen is still visible when it finishes. So after a
   sign-out — or after any mid-session `401` — the login form came back with the
   **password box still disabled**: the console was unusable without a hard
   reload. `logout()` now hands the form back and clears the error line. This
   defect did not exist in the old console (it never disabled the field), so it
   was a regression from this work, not a pre-existing bug.
3. `load()` ended with `if(currentTab===tab || currentTab==='overview')
   renderCurrent()`, so **every background prefetch that finished while the admin
   sat on the Overview rebuilt the entire screen** — KPI cards, alerts, the chart's
   month loop over the order rows, the 14 secondary metrics and the activity list.
   Only the three service queues have anything visible there; the other nine
   repaints changed nothing the admin could see. Measured with the wrapper in
   `admin_layout2.py COST=1` at 4× CPU throttling: **`renderOverview` ran 15 times
   for one login, 1,153 ms of self-time, plus 592 ms in `renderCurrent`**. Fixed by
   repainting for the service tabs only, through one shared animation frame
   (`queueOverview()`), while navigation, Refresh and a retry still paint
   synchronously. Same probe afterwards: **6 calls, 821 ms**, `renderCurrent` gone
   from the top costs. Nothing about what the screen shows changed.

### Functional checks actually executed

`admin_func.py` drives the production functions in a real Chrome at 390 × 844:
**111 of 111 assertions pass** — re-run against the final file after the last three
edits (`diag/func_final.txt`), not against an intermediate draft. They cover signing in and out, a wrong password,
`429`, an unreachable API, a stalled request, double-submit, session restore with
a good token / an expired token / a cold API, a mid-session `401`, all 20 sections
rendering content with no console error, every navigation destination resolving to
a titled module (no dead links), all six quick actions landing on the right page
(including "Payment issues" → orders filtered to `unpaid`), the filter narrowing
and then naming a term that matches nothing, unavailable counts, capped revenue,
a failing list endpoint reporting itself and recovering on "Try again", genuinely
empty tables, an order decision (confirm → `PATCH /api/admin/order/<id>/status` →
fresh read) and declining that confirm (nothing sent), the drawer opening and
closing, and the absence of decorative emoji in action labels.

### Regression evidence for this change

* `pytest -q` on the final file → **499 passed**, 115 pre-existing warnings,
  167.83 s (`diag/pytest_final.txt`).
* `ruff check app.py tests/` → **All checks passed**. (Local ruff 0.16.10; CI pins
  0.8.4 and was not re-run locally — same caveat as before.)
* `tests/sql` `npm test` → **12 order-write lockdown assertions passed** (untouched
  by this change, run to prove the admin rewrite did not drift the order rules).
* `node --check` on the extracted inline script → clean, 83,055 bytes of JS in one
  block (`diag/extract_js.py` → `diag/admin_script.js`).
* Live production was **not** re-measured for the authenticated paths: there is no
  admin password in this workspace, so `#401`-protected endpoints were exercised
  only through fixtures. The console's own Performance view now reports the real
  authenticated timings on the owner's screen, per request, including the network.

### A separate defect found while reading, not fixed here

`static/sw.js` registers **two `push` listeners and two `notificationclick`
listeners** (lines 30–47 are the current pair, 70–95 an older copy left appended).
Both `push` handlers call `showNotification()` with different tags
(`agribridge` and `agribridge-notif`), so one Web Push should surface as **two
system notifications**, and the older block points `badge` at `/icon-96.png`,
which does not exist in `static/`. This is verified in source only — no push was
sent. It is outside the admin brief, so it is reported here rather than bundled
into this change.

---

**Phase 1 COMPLETE — Baseline Audit**
**Completed:** 2026-10-10
**Auditor:** Claude Code (baseline inspection, no live environment access)

---

## What Was Inspected

| File / Area | Coverage |
|-------------|----------|
| `app.py` (1,907 lines) | Full read — all routes, auth helpers, service wiring, payment integrations |
| `static/index.html` (~6,685 lines) | Partial read (CSS + structure; JS reviewed for auth calls and hardcoded values) |
| `static/business-config.js` | Full read |
| `supabase/base-schema.sql` | Full read — all 21 tables, all RLS policies |
| `supabase/migrations/0001–0007` | Full read for 0001, 0002, 0004, 0007; skimmed 0003, 0005, 0006 |
| `services/config.py` | Full read |
| `services/orders.py` | Full read |
| `services/` (other files) | File list reviewed; individual files not read (discord, legal, matching, notifications, push, supabase_client, account, worker, business, monitoring, logging) |
| `tests/test_api.py` | Full read |
| `tests/` (other test files) | File list reviewed |
| `mobile/capacitor.config.json` | Full read |
| `mobile/package.json` | Full read |
| `render.yaml` | Full read |
| `requirements.txt` | Full read |
| `GO-LIVE.md` | Full read |
| `docs/` existing files | File list reviewed |

---

## Key Findings Summary

### Architecture (Sound)
- Flask backend on Render (2 gunicorn workers, Python 3.12)
- Supabase (Postgres + GoTrue Auth + PostgREST) for persistence and auth
- Single-file frontend (`static/index.html`) served as a PWA
- Capacitor shell for mobile (Android/iOS), wrapping the same HTML
- Africa's Talking for USSD (*789#) and SMS
- Flutterwave for payment (pluggable; off until keys are set)
- Gemini (crop doctor) + Groq (AI assistant) for AI features
- Discord + FCM push for operator notifications
- Sentry for error monitoring

### Security Posture (Mixed — better than average for stage, not production-ready)
- Server-side order creation with atomic stock reservation: DONE
- GoTrue identity verification (buyer_id from session, not body): DONE
- Fail-closed config validation on startup: DONE
- CORS restricted to known origins: DONE
- RLS enabled on all tables: DONE
- Admin login rate-limited: DONE
- Flutterwave webhook re-verification: DONE
- Legal consent framework built: DONE (not enforced)
- **Migration 0002 (blocking direct order inserts) and migration 0015 are both applied and the new SPA is live.** `create_order_atomic` is now category-aware (`orders.source_ref` returns 200, verified 2026-10-22). Livestock/supplier pricing resolves server-side from each item's own crop listing.
- **Admin panel uses shared-password JWT**, not Supabase identity
- **In-memory rate limiting and USSD sessions** — not safe with 2 workers

### Operational Readiness (Partial)
- SMS/USSD: sandbox only
- Payments: COD/direct MoMo only (no gateway live)
- Email receipts: not active
- Legal enforcement: off
- Farmer payouts: not built
- Database backups: documented but not automated

### Test Coverage (Good for API contract, thin for business logic)
- API contract tests: health, order auth, state machine, admin identity, webhook, push registration
- Missing: USSD route unit tests, livestock order path, matching service integration

---

## Outstanding Issues by Priority

### Must Fix Before Any Meaningful Live Traffic

| # | Issue | Action |
|---|-------|--------|
| 1 | Default admin password `agribridge2026` may still be set | Set strong `ADMIN_PASSWORD` in Render immediately |
| 2 | ~~Migration 0015 not applied~~ — RESOLVED 2026-10-22: 0015 is applied and `orders.source_ref` returns 200, so `create_order_atomic` is category-aware | Confirm a livestock and a supplier order price correctly in the live checkout |
| 3 | USSD sessions in-process dict — breaks with 2 workers and on restart | Switch to `ussd_sessions` table in Supabase |
| 4 | `farmer_ratings` table referenced in matching function does not exist | Create table in a migration |

### Should Fix Before Scale

| # | Issue | Action |
|---|-------|--------|
| 5 | In-process rate limiting | Switch to Redis-backed or Cloudflare edge rate limiting |
| 6 | `unsafe-inline` in CSP | Refactor JS out of the monolithic HTML file |
| 7 | Admin routes use self-signed JWT, not Supabase identity | Migrate to `require_admin_user()` |
| 8 | `pay_initiate` unauthenticated | Add `verify_supabase_user()` guard |
| 9 | `_auth_role()` reads client-writable `user_metadata` | Use `user_roles` table instead |
| 10 | Correlation ID logged without sanitization | Validate/strip header value |

### Should Do Before Play Store Release

| # | Issue | Action |
|---|-------|--------|
| 11 | `targetSdkVersion` not set in Capacitor config | Set to 34 in `capacitor.config.json` |
| 12 | No native push, biometrics, or deep links | Decide scope for native features |

---

## Phase 2 — Recommended Next Steps

1. **Security**: Apply the items in "Must Fix" above. Set a real `ADMIN_PASSWORD`. Confirm migration 0001 is applied to the live Supabase project (it may already be — no DB access to verify).

2. **DB migrations**: Apply migrations 0003, 0004, 0005, 0006 (all flagged as safe in their headers). Create the `farmer_ratings` table. Only then apply 0002 after the frontend livestock/supplier order cutover is done.

3. **Legal rollout**: Follow the six-step process in GO-LIVE.md §Legal. Apply migration 0007, deploy the updated web app, run `publish_legal.py`, smoke test, then set `ENFORCE_LEGAL_ACCEPTANCE=true`.

4. **Africa's Talking**: Move from sandbox to live API key + SMS sender ID. Test USSD end-to-end on a real handset.

5. **Payments**: Set up Flutterwave sandbox, run the full checkout-to-webhook flow, then go live.

6. **Email receipts**: Set up Resend, deploy the `send-receipt` Edge Function with `RESEND_API_KEY`.

7. **USSD session persistence**: Replace the in-memory dict with the `ussd_sessions` Supabase table.

8. **Rate limiting**: Add Redis to Render or switch to Cloudflare rate limiting rules.

9. **Backups**: Follow GO-LIVE.md §4 to set up GitHub Actions automated backup.

10. **Mobile**: Set `android.targetSdkVersion: 34` in `capacitor.config.json`. Run `cap sync android` and test the build. Address Play Store listing requirements.

---

## Files Created This Audit

- `docs/PRODUCTION_AUDIT.md` — full findings with severity ratings, route inventory, DB inventory, security analysis
- `docs/IMPLEMENTATION_STATUS.md` — this file

---

## Unread Files (Recommend Review Next)

These service files were listed but not read during this audit. Review them for their own security posture if making changes in those areas:

- `services/account.py` — account deletion logic
- `services/matching.py` — offer routing service
- `services/notifications.py` — notification dispatcher
- `services/push.py` — FCM integration
- `services/supabase_client.py` — Supabase wrapper
- `services/worker.py` — background matching worker
- `services/legal.py` — legal service
- `services/discord.py` — Discord notifier
- `static/index.html` (JS portion, lines 709–6685) — full frontend logic, all API calls, checkout flow
- `static/admin.html` — admin dashboard frontend
- `tests/conftest.py` and other test files — mock fixtures and coverage details
- `supabase/migrations/0003, 0005, 0006` — notification outbox, offer lifecycle, account deletion migrations
- `scripts/build_legal.py`, `scripts/publish_legal.py`, `scripts/migrate_supabase.py`
