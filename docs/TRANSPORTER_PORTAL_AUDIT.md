# Transporter portal — pre-implementation audit

Date: 2026-10-27. Scope: what exists today for a transporter, and the authoritative
delivery lifecycle. Written before any code change, per the portal brief §2 and §16
steps 1-2. Every claim below is either a `file:line` in this repo or a live probe
recorded here; anything not verified is labelled as such.

## 1. What a transporter can do today

Three surfaces mention transport at all, and only one of them is for a transporter.

| Surface | What it is | Transporter-reachable? |
|---|---|---|
| `static/transporter.html` (273 lines / 12.8 KB) | A real signed-in portal with four sections: Sign In, My Availability, My Profile, How it works | Yes — but **orphaned**: nothing links to it |
| `static/logistics.html` (58 KB) | "Logistics Command Center" — a **dispatcher/admin** screen gated by the admin password, 7 sections, Leaflet map, 60 s polling | No (admin credentials required) |
| `static/index.html` (884 KB SPA) | The marketplace. `transporter` appears **twice**, both marketing copy; the role chip at `:1810` has no `onclick` and no `href` | No transporter UI exists |

Discovery is the first real defect. `static/index.html` contains **zero** references
to `transporter.html` or `logistics.html`; the only inbound link in the whole repo is
`admin.html:278 → /logistics.html`. `manifest.json` shortcuts are
market/prices/doctor/dashboard. `wrangler.jsonc` is a plain assets directory with no
routes. A transporter must already know to type `https://agribrige.com/transporter`.

Worse, a transporter who signs into the main app is **silently turned into a buyer**:
`getCurrentRole()` (`index.html:9493-9499`) has no transporter branch and falls through
to `return 'vendor'`, and `ROLE_NAV` (`:9453-9459`) defines only
guest/farmer/vendor/hotel/supplier. The profile role `<select>` (`:8337`) offers
`['farmer','vendor','hotel','supplier']` — a transporter cannot even name their own role.

Live baseline of `/transporter` (measured in a browser, 2026-10-27): headings
`🚛 AgriBridge Transporter`, `Sign In`, `My Availability`, `My Profile`,
`How it works`; three fields (`email`, `password`, `availToggle`) and two buttons
(`Sign In`, `Sign Out`); 111 characters of visible text; **zero API calls on load**.
`/transporter.html` and `/logistics.html` return **307** — Cloudflare serves clean
URLs, so the canonical routes are `/transporter` and `/logistics`. Both currently
match their committed blobs (`5c31e064…`, `92b54a83…`).

## 2. The transport API surface that actually exists

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /api/transporter/me` (`app.py:3428`) | Supabase session, rate 60/60 | Reads the `transporters` row where `supabase_uid == uid`; 404 if not linked |
| `POST /api/transporter/availability` (`:3391`) | Supabase session, rate 30/60 | Writes `is_available` + `availability_updated_at` on **own** row only |
| `POST /api/transporter/location` (`:3446`) | Supabase session | GPS push. **Called by no frontend.** See §4 — it is broken in production |
| `POST /api/admin/orders/<id>/assign-transporter` (`:3886`) | admin | Writes `driver_name` / `driver_phone` **as free text**; audit event `order_transporter_assigned` |
| `POST /api/admin/orders/<id>/confirm-delivery` (`:3977`) | admin | The **only** way a delivery becomes `delivered` |
| `GET /api/admin/transporters`, `POST`, `PATCH`, `DELETE` (`:3323`, `:3335`, `:3366`, `:3381`) | admin | Transporter records are **admin-managed**; a transporter cannot edit their own vehicle/profile |
| `GET /api/admin/finance/transporter-settlements` (`:1804`) | admin | Returns `"available": False` with hardcoded `agreed_charge: None` — see §5 |
| `GET /api/track/<code>` (`:4191`) | public | Buyer-facing tracking |

There is **no** accept-job, decline-job, advance-delivery, proof-of-delivery or
earnings-read endpoint. `POST /api/orders/<id>/status` (`:2991`) accepts only an admin
or the row's `farmer_id`; a transporter gets 403 at `:3010-3011`.

## 3. The authoritative delivery lifecycle

Two tables carry status. **`orders.status` is authoritative** and is the only one with
enforced transitions; `deliveries.status` is a denormalised shadow with no validation.

`orders.status` — CHECK allows
`pending | confirmed | packed | in_transit | delivered | cancelled | disputed`
(`base-schema.sql:72-73`). The **legal** transitions are narrower, enforced in the
`set_order_status` RPC (`0015_category_order_pricing.up.sql:319-339`, mirrored by the
Flask whitelist at `app.py:3003`):

```
pending    -> confirmed | cancelled
confirmed  -> in_transit  | cancelled
in_transit -> delivered
```

`packed` and `disputed` satisfy the CHECK but are **unreachable** through the only
sanctioned writer. Anything else raises `illegal order transition % -> %`
(errcode `22023`).

`deliveries.status` — CHECK allows
`scheduled | picked_up | in_transit | at_checkpoint | delivered | failed`
(`base-schema.sql:89-90`), default `scheduled`. **No code in the repository ever sets
`picked_up` or `at_checkpoint`**; only `'scheduled'` (`app.py:3915`) and `'delivered'`
(`:4013`) are written, both by admin actions, via direct `supa_update` with no
transition check. `/api/admin/row` can write `status` freely (`:2550`).

Mapping the brief's seven workflow steps onto this: only step 1 (confirm an accepted
assignment) and step 7 (complete) have any server equivalent, and both are admin-only.
Steps 2-6 have no storage for the actor and no permitted transition.

## 4. Blocking schema gap — verified against production

`deliveries` has **no transporter linkage**. It stores `driver_name`, `driver_phone`,
`vehicle_plate` as free text (`base-schema.sql:88-90`). The string `transporter_id`
appears **zero times in any `.sql` file in this repo**. `app.py` nonetheless filters on
it at `:3476-3480` and writes `transporters.last_lat/last_lng` at `:3491-3493`.

Probed live against PostgREST with the publishable key (metadata only, no row data
retrieved):

| Probe | Result |
|---|---|
| `deliveries?select=transporter_id&limit=0` | **400 `42703` — column does not exist** |
| `deliveries?select=id&limit=0` | **200, `content-range: */0`** — anon may read, and the table holds **0 rows** |
| `transporters?select=last_lat&limit=0` | **401 `42501`** — grants deny anon, so its existence is **unverified** |
| `orders?select=transport_fee&limit=0` | **400 — does not exist** |

Consequences, stated plainly:

- `POST /api/transporter/location` cannot work; the filter references a column that is
  not there, so the write fails and the endpoint returns its own `503` path. Its test
  (`test_identity_and_admin.py:178-204`) asserting "503 on failed write" **passes
  because the feature is broken**. This is not the "free win" a source read suggests.
- "My Deliveries" is unanswerable: there is no way to ask which delivery is mine.
- Job exclusivity has no mechanism — no unique index, no conditional-update RPC, no
  trigger. `deliveries.order_id` is non-unique.
- `deliveries` currently holds **0 rows in production**, so an admin has never
  completed an assignment through this path at scale.

## 5. Transport money does not exist

`services/finance.py:20-28` already refuses to invent it:

> "Deliveries do not store an agreed transporter charge, so no transporter payable can
> be calculated yet."

`build_transporter_settlements` returns hardcoded `agreed_charge: None`,
`payable_status: "not_captured"`, `payment_ref: None`, `available: False`
(`finance.py:250-259`). `payouts` is farmer-only
(`base-schema.sql:108-119` — `farmer_id, gross_amount, commission_pct,
commission_amount, net_amount`; no `transporter_id`). The single money-minting trigger
`orders_create_payout` (`0016:100-102`) fires only on
`status='delivered' AND payment_status='paid' AND farmer_id IS NOT NULL` and never
reads `deliveries`. Ledger account `TRANSPORTER_PAYABLE` (`0013:38`) is seeded and
**never debited or credited by any migration**.

So an Earnings page can honestly show one of two things: nothing, or a real ledger
balance that does not exist yet. It must not show a computed "expected fee" as money
owed.

## 6. Security posture today (and three real defects)

Verified present and enforced:

- Every payment-status write requires an admin token (`:4089→:4099`, `:1628`, `:3977`);
  `/api/admin/row` explicitly rejects `status`/`payment_status` on `orders`
  (`:2580-2583`).
- `farmers.is_verified` / `is_premium` are admin-only columns (`_ADMIN_COLS`, `:2547`).
- `transporters` RLS is tight on cross-tenant access: `transporter read own` and
  `transporter update own availability`, both predicated on
  `supabase_uid = auth.uid()`, with `grant select, update` only — no INSERT/DELETE for
  `authenticated` (`0014:44-55`). Proven by `security_lockdown.test.mjs:115-119`.
- The API uses the **service-role** key, so RLS is bypassed server-side and Flask-side
  checks are the only authorization. No service-role key appears in any frontend.

Defects found, independent of this brief:

1. **`mark_notification_read` (`app.py:4452-4463`) has no ownership filter.** Its
   comment says "Only mark rows that belong to this user" and then calls
   `supa_update('notification_outbox', {...}, 'id', notif_id)` — any authenticated user
   can mark any other user's notification read.
2. **`deliveries_public_select … for select to public using (true)`**
   (`base-schema.sql:255`) lets an anonymous caller read every delivery row including
   `driver_phone`, `vehicle_plate`, `gps_lat/gps_lng`. Confirmed live (200). Currently
   harmless only because the table has 0 rows — the exposure is structural.
3. **The `transporters` UPDATE policy is row-scoped, not column-scoped.** A transporter
   may write every column of their own row, including `status`, i.e. self-reinstate
   from `inactive` to `active`. This is exactly the "bypass admin review" pattern the
   brief forbids, reachable directly over PostgREST.

Also relevant: the `uploads` bucket is `public = true` with a public-read policy
(`0012:9-33`), so **any proof-of-delivery photograph placed there is world-readable by
URL**. Proof-of-delivery needs a private bucket or a non-photo evidence method.
`static/_headers:9-20` records that admin/logistics/transporter declare **no CSP**, and
`logistics.html:343-344` loads Leaflet from cdnjs.

## 7. Existing behaviour a rebuild must preserve

- Optimistic availability toggle that **reverts on failure** (`transporter.html:222-239`).
- The 404 case handled distinctly from a network error: "Your account is not linked to
  a transporter profile yet" (`:185-188`).
- `h()` HTML escapers in both pages; quote-escaped CSV export (`logistics.html:987-998`);
  print receipt distinguishing paid from unpaid (`:963`, `:981`).
- GPS staleness thresholds (>15 min amber, >30 min queued) — `logistics.html:535, :588`.
- `debounceSearch` 400 ms (`logistics.html:521`).
- Known gap to fix, not preserve: `transporter.html` has **no token refresh and no 401
  handling**, so the session dies silently after ~1 h. It stores the whole GoTrue
  session including `refresh_token` in `sessionStorage` (`:159`).
- `sw.js:3` deliberately registers a worker that caches nothing and has no fetch
  handler, so there is no offline support anywhere.
- `logistics.html:1002` polls every 60 s with **no `document.hidden` guard** — the SPA's
  equivalent poll (`index.html:5904-5906`) does guard. Copy the guarded pattern.

## 8. Which of the five destinations is backed by real data

| Destination | Server capability | Data in production | Verdict |
|---|---|---|---|
| Home | partial (name, availability) | `transporters` rows exist | **Buildable now**, with honest empty states |
| Available Jobs | none | `deliveries` = 0 rows, no assignment column | **Blocked on schema** |
| My Deliveries | none ("mine" is unanswerable) | 0 rows | **Blocked on schema** |
| Earnings | explicitly `available: False` | no fee captured anywhere | **Blocked on a fee decision + ledger postings** |
| Profile | read-only | real columns | **Buildable now**; editing is admin-only by design |

## 9. Test coverage to extend, not duplicate

`security_lockdown.test.mjs` (0014 grants/RLS), `order_write_lockdown.test.mjs` (0019),
`settlements.test.mjs` (0016 payouts), `ledger.test.mjs` (0013 `post_journal`),
`category_orders.test.mjs` (0015), `pesapal.test.mjs` (0017), `farmer_ratings.test.mjs`
(0018), `legal_consent.test.mjs` (0007). Python: `test_admin_finance.py`
(assign/confirm/settlements), `test_identity_and_admin.py` (location, admin row editor),
`test_api.py` (order status), `test_tracking.py`, `test_service_requests.py`.

**No SQL test touches `deliveries`, transport assignment or proof of delivery.**
Untested transport routes: `POST /api/transporter/availability`,
`GET /api/transporter/me`, all `/api/admin/transporters*`,
`GET /api/admin/active-deliveries`, `GET /api/admin/deliveries`.

`0018` (farmer ratings view) and `0019` (lock farmer order writes) are **written but not
applied**. The portal does not depend on `0018`. It should not present any earnings
claim that rides on `payouts` before `0019` is applied, because that table is currently
forgeable by a farmer PATCH.

---

## 10. Stage 1 delivered: the schema the portal needs (written and tested, **not applied**)

Chosen order was "backend, then full portal", so this stage changed no UI. Two
migrations and two SQL test files.

### `0020_transport_assignment` — linkage, money, proof, exclusivity, RLS

Adds 19 nullable columns to `deliveries` plus `transport_rate_config`,
`transport_quote()` and `claim_delivery()`. The three decisions that shaped it:

- **No invented price.** `transport_fee_ugx` is nullable with no default and
  `transport_rate_config` is created **empty**. `transport_quote()` returns
  `{'status':'not_configured', 'message': …}` rather than a plausible zero,
  because a zero reads as "free" to a driver. An operator must enter real fuel
  and handling rates before any fee exists.
- **The recipient code is stored hashed.** The owner asked for code + photo +
  admin confirmation. A plain code in a column the assigned transporter can
  select would let them complete a delivery without meeting the buyer, which is
  worse than today, so only `proof_code_hash` is stored and it is compared, never
  returned.
- **Exclusivity is enforced twice.** `claim_delivery()` is a `security definer`
  conditional update that can land only while `transporter_id is null`, and
  `deliveries_one_live_assignment_idx` is a partial unique index on `order_id`
  for live statuses. The function is the mechanism; the index is the backstop if
  a future caller forgets it. `delivered`/`failed` are excluded so a retried job
  stays possible.

It also closes the anonymous read found in §6: `drop policy
deliveries_public_select`, service-role-only writes, and
`transporter reads own deliveries` for `authenticated`.

### `0021_proof_of_delivery_bucket` — where a proof photograph may live

§6 established that the only bucket, `uploads`, is `public = true` with a
`to public` select policy (0012:9-16, 29-33), so a proof image placed there is
world-readable by URL. 0021 creates `proof_of_delivery` with `public = false`,
8 MiB, `image/jpeg|png|webp` only, and **no policy at all** — RLS denies by
default, and a grep across `supabase/` confirms 0012's policy is the only
`storage.objects` policy in the project, so the new bucket inherits no read path.
0020 deliberately stores an object *path*, not a URL, for this reason. The backend
writes it and would serve a short-lived signed URL after checking ownership
server-side, which keeps one authorization rule instead of a policy that parses
object names.

### Two defects the tests found in my own first draft

Worth recording, because both are the kind that only a real engine catches:

1. `transport_quote()` was an INVOKER `language sql` function reading a table
   the same migration revokes from every client role. The backend's own call
   failed `42501 permission denied for table transport_rate_config`. It is now
   `security definer set search_path = public, pg_temp`.
2. `revoke all … from public, anon, authenticated` also removes the grant PUBLIC
   provides, and Supabase default privileges cannot be assumed — PGlite proved
   `service_role` had no access to the new table. `grant all on
   transport_rate_config to service_role` is now explicit, for the same reason
   `claim_delivery()` has an explicit `grant execute … to service_role`.

### Verified / not verified

| Claim | Basis |
| --- | --- |
| 0020 and 0021 apply, re-apply and roll back | `tests/sql/transport_assignment.test.mjs` (39 assertions), `tests/sql/proof_bucket.test.mjs` (14) |
| A second claim loses and cannot overwrite the winner's fee | same, `already_claimed` + `transport_fee_ugx` unchanged |
| A direct write of a second live assignment is rejected | same, `23505` on the partial unique index |
| anon reads 0 delivery rows and 0 proof objects, while still reading `uploads` | same, with control rows in both buckets so a zero means RLS, not an empty test |
| A client cannot call `claim_delivery()` or `transport_quote()` | same, `42501` |
| Rollback does **not** restore the anon read, client writes, or a bucket holding evidence | same |
| Rates are not invented when unconfigured | same, `not_configured` and `count(*) = 0` on the rate table |
| Migration hygiene (paired down, transaction, idempotent, no secrets, ordered) | `pytest tests/test_migrations_hygiene.py` → 5 passed |
| Whole SQL suite still green | `npm test` in `tests/sql` → 10 files, 203 assertions, exit 0 |
| **Applied to production** | **No.** Not run against `lvloaweijidgcuezdnmu`; the live `42703 column "deliveries.transporter_id" does not exist` is still the current production state |
| Two claims racing in real wall-clock time | **Not measured.** PGlite is one backend, so claims run sequentially; that exercises the conditional update, and the unique index is what makes a true race safe. Proving the race itself needs two connections to a real Postgres |
| Flask endpoints exist for any of this | **Yes, since stage 2 — see §11.** Before stage 2 nothing could call these functions |

## 11. Stage 2 delivered: the API the portal is allowed to call (written and tested, **not deployed**)

Task #72. Every endpoint below is Supabase-session authenticated
(`verify_supabase_user`, which asks GoTrue who the caller is) or admin-JWT
authenticated (`verify_token(role='admin')`). No endpoint accepts an identity
from the request body.

### Transporter endpoints

| Route | Behaviour, and the refusal it is built around |
| --- | --- |
| `GET /api/transporter/jobs` | Unassigned + `scheduled` only. No counterparty contact fields; an unrecorded fee is `fee_state: not_set`, never 0 |
| `POST /api/transporter/jobs/<id>/claim` | Body ignored. `claim_delivery()` decides exclusivity under a row lock; a loser gets 409 `already_claimed`, an inactive account 403 before any RPC |
| `GET /api/transporter/deliveries` | Own jobs only, with the farmer's name/phone **because ownership was already established** by the query and RLS |
| `POST /api/transporter/deliveries/<id>/status` | Only the steps in `_NEXT_DRIVER_STATUS`; `delivered` is not a driver step. The write's WHERE contains the old status, so a double tap or a race returns 409 `changed_elsewhere` with the real current status |
| `POST /api/transporter/deliveries/<id>/offer` | Writes `transporter_offer_ugx` + `offer_status='proposed'` and **never** `transport_fee_ugx`. 409 `offer_pending` while one is awaiting a decision |
| `POST /api/transporter/deliveries/<id>/proof` | Multipart code + photo. Photo is validated by magic bytes, stored in the private bucket, then `submit_delivery_proof()` (0023) compares the SHA-256 digest under a lock and counts wrong guesses. Wrong code → 200 with `code_matched:false` and the photo kept as evidence, never "success". Five wrong guesses → 429. Storage failure → 503 with nothing recorded, and a refused submission deletes its orphan object |
| `GET /api/transporter/earnings` | States `payment_tracking: "not_available"` in the payload. No withdrawable balance is computed; `fee_*_total` sums only fees the record actually holds |
| `PATCH /api/transporter/profile` | `phone`/`email`/`district` only. `vehicle_type`, `capacity_kg`, `plate_number`, `status`, `is_available`, `supabase_uid`, `notes` → 403 `field_requires_admin` naming the fields |
| `GET /api/transporter/dashboard` | Five `supa_count` values; a failed count is `null` and listed in `counts_unverified`, never 0 |
| `POST /api/transporter/availability` | Unchanged from before; save failure returns a non-2xx so the UI can revert (§4's rule) |

### Admin endpoints (supporting the portal, not replacing it)

`POST /api/admin/deliveries/<id>/proof-code` (6-digit `secrets` code, shown
once, only the hash stored, never logged), `GET …/proof` (metadata + a 300 s
signed URL; `proof_code_hash` is never selected), `POST …/proof/reject` (clears
the submission so the driver can retry, keeps the photo), `POST …/offer`
(accept/reject; accepting is the only path that turns a proposal into
`transport_fee_ugx` + `fee_basis='negotiated'`), `POST …/fee` (sets the figure,
its basis and optionally the quote snapshot), `POST /api/admin/transport-quote`
(returns `not_configured` verbatim), `GET|PUT /api/admin/transport-rates`
(empty by default; negative or malformed figures → 400).

`admin_confirm_delivery` now refuses **before** it changes anything when a
recipient code has been issued and the delivered proof is missing
(409 `proof_missing`) or mismatched (409 `proof_code_mismatch`), and records
`proof_verified_by`/`proof_verified_at` when it does confirm. Jobs with no
issued code keep the pre-existing behaviour, because refusing every historical
order would break a working admin flow to enforce a rule nobody opted into.

### Two bugs the tests found during stage 2

1. `return jsonify({...}, 409)` — the status code was an argument to
   `jsonify`, not a tuple element, so a refused reassignment returned **200**
   with a body of `[{…}, 409]`. Caught by
   `test_admin_cannot_quietly_move_a_live_job_from_its_driver`; fixed, and a
   grep for the same shape found no other occurrence.
2. `proof_verified_by` and `transport_rate_config.updated_by` were `uuid`, but
   the admin console's identity is the legacy JWT subject `'admin'`. Writing
   them would have failed on the only identity the console issues. Both are
   `text` in 0020 now, with the reason in the migration.

Also corrected: 0020's first draft claimed `supabase_uid` was client-writable;
`with check (supabase_uid = auth.uid())` already blocked it. The header now says
so, and 0022's test asserts the refusal both before and after it applies.

### Verified / not verified (stage 2)

| Claim | Basis |
| --- | --- |
| Identity comes from the session, not the body; body `transporter_id`/`fee_ugx` ignored | `tests/test_transport_portal_api.py` |
| Lost claim / lost status race reported as lost; illegal steps refused | same, 409 paths |
| Counter-offer never writes an agreed fee; admin acceptance does | same |
| Earnings expose no withdrawable balance | same, asserts `payment_tracking == "not_available"` |
| Proof: matching code, wrong code, lockout, spoofed file, missing photo, storage failure | same (6 tests) and `tests/sql/transport_assignment.test.mjs` §9 (11 assertions) |
| Admin confirm requires a matched proof once a code was issued | same |
| Quote says `not_configured`; rate table empty and validated | same |
| Whole Python suite | `pytest -q` → **529 passed** |
| Whole SQL suite | `npm test` in `tests/sql` → **12 files, 225 assertions, exit 0** |
| Lint | `ruff check app.py services/supabase_client.py tests/test_transport_portal_api.py` → clean (local 0.16.10; CI pins 0.8.4) |
| **Deployed / applied to production** | **No.** 0020–0023 are unapplied and nothing new has been pushed or deployed |
| End-to-end portal workflow in a browser | **No.** The five-destination UI is stage 3 (task #73); today these routes are only exercised by pytest |
| Two claims across two real connections | **Not measured** (PGlite is a single backend; the unique index is the guarantee) |

## 12. Stage 3 delivered: the five-destination portal (written and exercised, not deployed)

`static/transporter.html` was rewritten (273 → 1,237 lines) around the five
destinations §3 requires. What it talks to is in §11; what it is *allowed* to
claim is narrower on purpose.

| Destination | Reads | Writes | Honest states |
| --- | --- | --- | --- |
| Home | `/api/transporter/dashboard` + own deliveries | availability toggle → `POST /api/transporter/availability` | A count that failed is `—` with "Could not be measured", never 0; a null fee is "Fee not set yet" |
| Available jobs | `/api/transporter/jobs` | claim → `POST …/jobs/<id>/claim` | No farmer/buyer contact before the job is yours; "Open", not the internal `scheduled` |
| My deliveries | `/api/transporter/deliveries` | status step, counter-offer, proof | Only the next legal step is offered; `at_checkpoint` has no "complete" button |
| Earnings | `/api/transporter/earnings` | none | "No balance to withdraw"; a completed job is not "paid" |
| Profile | `/api/transporter/me` | phone / email / district | Vehicle, plate, capacity and status are read-only with the reason stated |

Server-enforced rules carried into the UI: a claim goes through a confirmation
sheet (an accidental double tap cannot accept a job), a lost race shows the
real status instead of a success, a 401 refreshes the token once and otherwise
signs out loudly, and the unlinked state replaces the whole portal with "Account
not linked" rather than an empty jobs list.

### Discovery and role routing fixed

Before this stage a transporter had no inbound link and was turned into a buyer
by `getCurrentRole()` falling through to `'vendor'`. Now:

* `static/index.html:1810` — the landing page's Transporter chip is an
  `<a href="/transporter">` ("Driver portal →"); the footer has the same link.
* `getCurrentRole()` maps `transporter`/`driver` explicitly.
* `applyRoleUI()` sends that role to `/transporter` instead of painting a
  buyer's navigation. Verified both ways: a `vendor` session stays on `/` with
  the market tab; a `transporter` session is redirected.

### Counter-offering a fee

The owner asked that the driver negotiate a fee in their own portal "so they
also know the fee they are to get", while the figure the customer is charged
stays an administrator's decision. The portal therefore treats a proposal as a
proposal:

* A live job the driver holds shows **Propose a different fee**. The sheet is a
  single whole-UGX amount, prefilled with the current fee when one exists, and
  says plainly that an administrator has to accept it and that the buyer is not
  told it.
* `POST /api/transporter/deliveries/<id>/offer` writes `transporter_offer_ugx`
  and `offer_status='proposed'` only. It never touches `transport_fee_ugx` —
  `app.py:3843` — so a proposal cannot make Finance owe anyone money.
* While a proposal is open the card replaces the button with "Your fee offer is
  awaiting an administrator's decision." A declined offer is stated as declined
  and may be proposed again; the server refuses a second open proposal with
  `409 offer_pending`, and the portal closes the sheet and shows that message.
* Client-side the amount is constrained by `min`/`max` on the input *and*
  re-checked in `submitOffer()` before any request is made.

### Admin console can operate the new endpoints

`static/admin.html` Deliveries now shows fee, proof state and any proposed
offer, with `Fee`, `Code`, `Proof` and `Accept/Decline` actions wired to the
§11 endpoints. The code is displayed once in a prompt and never stored
readable; the proof photo opens through a 5-minute signed URL.

The Transporters editor also gained **Login UID**. Without it the portal could
never be reached: `/api/transporter/*` resolves the caller through
`transporters.supabase_uid`, and no screen could set that column — every driver
would have been told "account not linked" forever. `POST/PATCH /api/admin/row`
now accepts it, rejects anything that is not a UUID (`400 invalid_login_uid`)
and refuses to link one login to a second driver (`409 login_already_linked`).

### Verified / not verified (stage 3)

| Claim | Basis |
| --- | --- |
| Inline scripts parse | `node --check` equivalent (vm.Script) on `transporter.html`, `admin.html`, `index.html` — 9 blocks, 0 errors |
| No horizontal overflow at 320/360/390/430/768/1024/1280 px | Measured in a browser: `scrollWidth - clientWidth == 0` at every width |
| Touch targets | Bottom nav 64 px; desktop sidebar 46 px |
| Availability round-trip | Browser: Offline → POST → Available pill + toast, switch state re-read from the server |
| Claim needs confirmation; open jobs hide contacts | Browser: sheet shows route + fee, Cancel focuses first, no `.contact` on open cards |
| Proof sheet | Browser: 6-digit `pattern`, `inputmode=numeric`, `autocomplete=one-time-code`, photo `accept` list + `capture=environment`; empty file blocked client-side |
| Counter-offer, all three states | Browser (stubbed): pending → "awaiting an administrator's decision" and no button; none → button; declined → "Your last fee offer was declined." plus button |
| Counter-offer validation | Browser (stubbed): 500 UGX blocked before any request (`checkValidity()` false, sheet stays open, no network entry); 125,000 sent as exactly one `POST …/offer` |
| Counter-offer conflicts | Browser (stubbed): a second proposal returns `409 offer_pending`, sheet closes, toast shows the server's own sentence |
| Counter-offer is only offered on jobs the driver holds | Browser (stubbed with `status: scheduled` present): Available jobs → 2 cards, 2 "Open" pills, **0** propose buttons; My deliveries → 3 cards, 2 propose buttons |
| Offer sheet layout | Measured at 320 px with the sheet open: `scrollWidth - clientWidth == 0`, sheet 304.8 px, input 271.2 px |
| Unlinked login | Browser (404 stubbed): "Account not linked", navigation hidden, sign-out only |
| Expired session | Browser (401 + failed refresh stubbed): session cleared, "Your session expired. Sign in again." |
| Earnings wording | Browser: payment note and "no balance to withdraw" present; no withdraw button |
| Regressions | `pytest -q` → **480 passed, 3 e2e modules skipped** (Playwright's bundled Chromium cannot be downloaded behind this machine's TLS interception); the three modules were then run against the installed Chrome through a throwaway launch shim, tests unmodified → **49 passed**. Total **529 passed, 0 failed**. `npm test` (tests/sql) → **225 assertions, exit 0** across 8 files; `ruff check` clean; `scripts/build_legal.py --check` up to date |
| **Screenshots** | **Not captured** — the in-app browser had no visible surface; layout was measured from the DOM instead |
| **Against the real backend** | **No.** The browser session used a localhost stub serving the real HTML with synthetic API bodies, because 0020–0023 are unapplied and no transporter credentials exist here. The API contract itself is covered by pytest |
| Admin console actions end to end | **Not exercised in a browser** — `admin.html` needs the admin password, which is not in this environment |
| Deployed | **Yes** — pushed as `ed838fe` on 2026-10-28; see "Released" below |

### Released (verified against production, not inferred)

| Check | Result |
| --- | --- |
| GitHub Actions on `ed838fe` | `CI` → success; `Deploy public web app` → success (read from the public runs API) |
| The published portal is the committed file | `agribrige.com/transporter` → 200, **61,951 bytes**, sha256 `52b52ef5eaaadb7c…` — byte-identical to `git show HEAD:static/transporter.html`. The local working copy differs only by CRLF line endings |
| The portal opens as a guest | Browser, live: "Transporter sign in" visible, bottom nav hidden, `scrollWidth - clientWidth == 0` |
| The landing page links to it | Browser, live: the Transporter chip is a visible `<a href="/transporter">` (225×126 px, no underline) beside the other four chips; page overflow 0 |
| The new API is live | `GET /api/transporter/dashboard` → **401 `{"error":"Missing token"}`**. The route did not exist at `4214074` (`grep` on that blob: 0), so this is new code answering, not a cached 404 |
| The admin transport routes are live | `GET /api/admin/transport-rates` → 401; `POST /api/admin/transport-quote` → 405 on GET (route exists, method not allowed) |
| Existing pages unaffected | `/` → 200, `/admin` → 200, `/logistics` → 200 |
| GitHub Actions on `a26f5cd` (docs) | `CI` → success |
| GitHub Actions on `74155ca` (admin link) | `CI` → **failure** — see below. `Deploy public web app` → success |
| GitHub Actions on `b18cfad` (test fix) | `CI` → **success**, **532 passed**, ruff clean — read from the job log, not inferred |

#### The CI failure on `74155ca`, and what it was

`CI` failed in "Unit + integration + browser tests" with **1 failed, 531
passed**:

```
FAILED tests/e2e/test_farmer_ui.py::test_farmer_nav_is_five_named_tabs_and_sell_is_the_lead_action
TypeError: 'NoneType' object is not subscriptable   (line 366: sell.bounding_box()["y"])
```

The cause is a race in the browser fixture, not a regression from the admin
change: `_signin` waited only for `#roleHomePanel .fh-sell` to **exist in the
DOM**, but the role home panel is filled asynchronously and the page is swapped
in after that. When the test measured it before the swap, the node had no box —
Playwright answers `bounding_box()` with `None`, and `inner_text()` still returns
text for a hidden node, which is why the checks above it passed. It passed on
this machine and on CI at `a26f5cd` purely on timing.

Fix (`b18cfad`): the fixture now waits for the card to be **visible**
(`locator.wait_for(state="visible")`), and the assertion asserts the box exists
with a sentence that names the reason, instead of raising `TypeError`. No
assertion was removed or loosened; the layout requirements (sell card above the
stats, wider than a stat tile, ≥44 px) are unchanged. Verified by running the
farmer module three times (21 passed each) and the full suite locally (532
passed), then by the green CI run above.

This commit touches only `tests/`, so no `static/` asset was redeployed and the
live bundle is unchanged by the fix.

**One further failure that is not explained.** The first local run that included
the new service-worker tests (2026-10-29) failed
`tests/e2e/test_farmer_ui.py::test_profile_splits_editable_from_verified_details`
once. Its traceback was not kept, and **20 further runs passed** (12 with that
test alone, 5 of the farmer module, 3 of the whole suite at 537 passed). It is
recorded as unexplained rather than papered over: the backend CI job now
publishes each failing test's name and message as a public `::error::`
annotation (the same way the SQL job does), so a recurrence will be readable
without a GitHub login. That test is the one to look at first if CI goes red.

**Still required before a driver can use it:** migrations 0020–0023 applied to
Supabase, then a `transporters` row with its **Login UID** filled in. Until
then the portal's API calls return a loud error and nothing is silently
invented.

### What happens on the first push (checked before releasing)

Pushing `static/**` to `main` runs `deploy-cloudflare.yml` (a real `wrangler
deploy`), and `app.py` reaches Render — so a push is a release. Four things were
checked first, because 0020–0023 are still unapplied:

* **The admin console does not break.** `renderDeliveries` reads every new field
  through a null guard, and `/api/admin/deliveries` selects `*`. With the
  columns absent, fee prints as `unset`, proof as `—` and no offer row appears —
  the screen still lists deliveries and still has Edit/Delete.
* **No CSP blocks the portal.** `static/_headers` deliberately applies no CSP to
  `transporter.html`, so its calls to the Render origin are not filtered.
* **`/transporter` resolves on the Workers bundle.** Measured live before the
  change: `agribrige.com/transporter` → `200 text/html`, and `/admin.html` →
  `307` to `/admin`, i.e. the assets handler serves extensionless paths. The
  links added to `index.html` use that form.
* **The identity lookup cannot fail on a missing column.** `transporters` and its
  `supabase_uid`/`is_available`/`availability_updated_at` columns predate this
  work (0014 policies and the old portal already used them).

Consequence while the migrations are unapplied: `/api/transporter/*` returns a
loud error instead of data, and the fee/code/proof admin actions return 503
"Nothing changed". Nothing that worked before this change starts failing.
