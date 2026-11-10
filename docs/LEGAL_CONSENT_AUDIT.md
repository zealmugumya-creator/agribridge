# AgriBridge Legal & Consent — Phase 1 Audit (L1)

**Date:** 2026-11-09 · **Brief:** "Legal & Consent Management System" (15 phases, attachment `e1a8b444`)
**Baseline:** repo `main` @ `979ade3` (identical to `origin/main`), plus anonymous live probes of
`https://agribrige.com` (SPA) and `https://agribridge-1-og7a.onrender.com` (API).
**Method:** direct source inspection; production probes without an authenticated session.
No database credentials, Render dashboard, Supabase dashboard or Cloudflare dashboard were
available — anything needing them is marked **UNMEASURABLE** with a census query or dashboard
step for the owner. Nothing was modified before this report.

Evidence labels: **[S]** verified in source at 979ade3 · **[P]** verified in production by probe ·
**[U]** unmeasurable from this workstation.

---

## 1. What already exists (do not rebuild)

| Brief phase | Existing mechanism | Evidence |
|---|---|---|
| 3 — registry | `legal_document_versions` (0007): doc_type+version PK, sha256, effective_at, is_material, requires_acceptance, applies_to_roles; **immutable once published** (trigger refuses content changes and deletes); `legal_publication_log` table exists but nothing writes to it | [S] `supabase/migrations/0007_legal_consent.up.sql:24-74` |
| 4 — consent records | `legal_acceptances`: id, user_id, doc_type+version FK, content hash, account_role, method, evidence jsonb, **server-generated** `accepted_at default now()`, unique(user,doc,version); **append-only** (UPDATE/DELETE refused by trigger); RLS self-read only; anon/authenticated write grants revoked; writes only via service-role RPC `record_legal_acceptance` which verifies the published hash | [S] 0007:77-91, 130-150, 172-230 |
| 4 — optional vs mandatory | `marketing_consent_events` (append-only, channels sms/email/whatsapp gated by `business.MARKETING_CHANNELS`) + `marketing_consent_current` view; separate from contractual acceptance | [S] 0007:94-109; `services/legal.py:242-280` |
| 5 — email signup | Checkbox required before `signUp` (`regAcceptTerms`, index.html:10408); ticked versions travel in `user_metadata.legal_signup`; `POST /api/legal/sync-signup` records them on first session (only current versions, only if ticked, account ≤30 days) | [S] index.html:10408-10424, 10601; `services/legal.py:199-233` |
| 5 — Google OAuth | Locked onboarding modal (`modal-locked`, no dismiss) with **required** `oauthAcceptTerms` checkbox (10552, 10584, 11631); acceptance recorded through the same API; `postAuthLegalCheck()` runs after login, signup, OAuth onboarding, session restore and role change (10369, 10459, 10623, 10150, 10179, 8816) | [S] |
| 5/9 — re-acceptance | `postAuthLegalCheck` → `/api/legal/status`; outstanding docs open the **locked** review modal (Escape/overlay only close non-locked modals, 8854; exits are sign-out / privacy request / support). Stale version → 409 → re-review. Material updates re-prompt; non-material corrections don't (`last_material_version` floor, `_satisfied`) | [S] index.html:11316-11329, 12202-12256; `services/legal.py:120-127` |
| 6 — backend gate | `legal_gate()` fails **closed** (503 on check failure), returns 403 `legal_acceptance_required` + outstanding list — but **OFF by default** (`ENFORCE_LEGAL_ACCEPTANCE`, app.py:120) and wired to only **7 routes**: uploads, listings POST/PATCH/media PATCH, orders POST, requests POST, offers accept | [S] app.py:576-593, 2003, 2085, 2155, 2221, 2981, 3211, 3254 |
| 11 — user settings | Privacy settings UI (marketing toggle, privacy requests), accepted-history API `/api/legal/history` + SPA viewer (12323), profile-menu links to Terms/Privacy, account deletion `/api/account/delete` (0006; open orders → deferred deletion request) | [S] app.py:6878-6956 |
| 12 — Uganda law | `docs/LEGAL_PENDING.md` already tracks every missing identity detail and 11 advocate questions (DPPA 2019, PDPO registration, retention, refunds, escrow-negative wording, fees) | [S] |
| 14 — tests | 50 tests in `tests/test_legal.py` + build-freshness + migration hygiene; full suite 730 passed on 979ade3; CI check-runs all green | [S/P] |

Documents today (manifest, all 1.0.0): terms-of-use (accept), privacy-policy (acknowledge),
buyer-terms, farmer-terms, supplier-terms, exporter-terms. Build pipeline `scripts/build_legal.py`
regenerates HTML + versioned archives + manifest; `test_legal_build` pins byte-freshness.
Documents are reachable after registration (footer, profile menu, review modal) and versioned
archives are public. **[S]**

## 2. Production probes (2026-11-09, anonymous)

* SPA `index.html` is **byte-identical** to 979ade3 (sha256 `07068c0c…`). E1 is live: backend
  manifest serves `exporter` → exporter-terms + privacy + terms. **[P]**
* **DIVERGENCE — transporter-terms exists in production but not in git.** The backend manifest
  serves `transporter-terms` 1.0.0 (sha256 `4fff3628…`, effective 2026-11-08) and
  `role_documents.transporter`; the backend serves its HTML (9,999 bytes) and versioned archive
  (both HTTP 200). The repo has **no** transporter-terms source, HTML or manifest entry, and no
  commit on any fetched branch ever touched one. The static host (agribrige.com) 404s the
  document and its manifest lacks the entry. Evidence preserved:
  `diag/live_transporter_terms_1.0.0.html`, `diag/live_legal_manifest_2026-11-09.json`. **[P]**
  * Provenance (which bundle Render runs, who deployed it) is **[U]** — needs the Render dashboard.
  * Consequence in main: `normalize_role('transporter')` → `vendor` (services/legal.py:41-52) and
    the SPA's `normLegalRole` agrees — so a **transporter is asked to accept the Buyer Terms**.
    The production manifest's transporter key is dead unless the live `legal.py` also differs. **[S]**
  * Hazard: if the live DB already holds transporter-terms 1.0.0 @ `4fff3628…`, the immutability
    trigger makes `ensure_published` **503** if the repo ever publishes 1.0.0 with a different
    hash. Reconciliation must reproduce the live bytes or use a new version — owner decision. **[S]**
* `ENFORCE_LEGAL_ACCEPTANCE` value on Render: **[U]** (dashboard). Default in code is OFF.
* Migration 0007 applied in production: **[U]** — census below. (CI proves it runs on PGlite, not
  that the owner applied it.)

## 3. The brief's Phase 1 questions, answered

1. **Missing documents:** Cookie Policy, Payment & Refund Policy, Delivery & Dispute Resolution
   Policy, Acceptable Use Policy, Transporter Agreement (in the repo). No Legal Centre UI —
   documents are standalone HTML pages, not searchable, no TOC/print controls beyond browser defaults.
2. **Outdated/incomplete:** current documents are honest but carry "to be confirmed" placeholders
   (LEGAL_PENDING.md) pending owner details and advocate review.
3. **Existing consent records:** [U] — census below.
4. **Do Google users bypass consent?** Frontend: no (locked onboarding + required checkbox +
   postAuthLegalCheck on every session path). Backend: the gate is OFF by default, covers 7 routes,
   and does not cover direct PostgREST writes (§4).
5. **localStorage-only consent?** Contractual acceptance: no — server DB (0007). The **cookie
   banner** choice IS localStorage-only (`ab_cookies`), with no categories and no server record
   (index.html:8482-8483, 12067-12070). Its "no advertising trackers" claim matches the code (no
   analytics/marketing scripts found).
6. **Protected APIs without acceptance?** **Yes.** Even with the flag on, `/api/pay/initiate`,
   `/api/pay/card`, `/api/pay/card/validate`, all 12 `/api/transporter/*` routes and profile writes
   are ungated; and the SPA still writes some tables **directly to PostgREST** (§4).
7. **Does a document change invalidate acceptance?** Material: yes (version compare + floor).
   Non-material correction: no (by design).
8. **Documents accessible after registration?** Yes (footer, profile menu, review modal, archives).
9. **Admins manage versions?** **No.** Publication is build-time (`publish_legal.py` /
  `ensure_published`); no draft/edit/schedule UI, no consent statistics, no outstanding-user list,
  no export; `legal_publication_log` is never written.

## 4. The enforcement gap that matters most

The SPA writes these tables **directly to Supabase PostgREST** with the user's own token
(`sbInsert`/`sbUpdate`, index.html:3439/3487), so the backend gate never sees them:

| Table | Call site | Nature |
|---|---|---|
| `animal_listings` INSERT | 4834 | **a farmer publishes a listing without any acceptance check** |
| `supplier_products` INSERT/UPDATE/DELETE | 6345, 6354 | supplier catalogue management |
| `reviews` INSERT | 6894 | post-order review |
| `farmers.profile_photo` UPDATE | 8027, 8721 | profile write |
| `community_posts`, `disease_reports`, `vet_bookings`, `price_alerts`, `cart_sessions` | various | community / extension / convenience |

RLS permits these self-writes (0026). The brief's final acceptance condition — *backend-verified
acceptance before protected functionality, for every auth method and existing users* — therefore
needs either (a) moving the remaining writes behind the API, or (b) a **database-layer guard**
(trigger checking `legal_acceptances` for `auth.uid()`) that covers both paths. Option (b) is
additive and does not replace the auth system; it needs a DB-resident "current required versions"
source (today "current" lives in the manifest file). Design decision for L2, owner approval before
any migration is applied.

Interpretation to confirm: **public catalog browsing stays public** (the homepage brief requires
real products visible to anonymous visitors); *transactional* actions (order, pay, list, deliver,
review) are the protected surface.

## 5. Gap list → L2 work plan (brief phases)

* **P2** Legal Centre (searchable, TOC, version/effective dates, print) + Cookie, Payment & Refund,
  Delivery & Dispute, Acceptable Use documents; Transporter Agreement reconciled with the live
  bytes. Missing identity facts stay admin-managed placeholders marked "requires legal review".
* **P3** Registry exists; add publication-log writes and a DB-resident current-version pointer.
* **P4** Exists; add auth-provider (google/email) to acceptance evidence; no other schema change.
* **P5/P6** Extend the gate to payments, transporter ops, profile writes; close the PostgREST
  bypass (§4); keep legal/auth/sign-out/deletion endpoints exempt; flag stays OFF until coverage
  is proven and the owner approves turning it on.
* **P7** Transporter role mapping fix (backend + SPA) with the reconciled document; role-change
  re-check already exists (8816).
* **P8** Cookie preference centre: essential/analytics/marketing categories, optional default-off,
  server-recorded with policy version; honest "only essential used today" wording; no tracker
  invented to fill a category.
* **P9** Material-update flow exists; wire admin publishing to it (P10).
* **P10** Admin Legal & Compliance: drafts, publish (new immutable version), effective dates,
  roles, mandatory flag, history, consent stats, outstanding users, export; sensitive actions
  logged; ordinary admins cannot rewrite history (append-only already enforces this).
* **P11** User Legal & Privacy: accepted versions + dates (API exists, extend UI), marketing prefs
  (exist), cookie prefs (new), privacy requests + deletion (exist).
* **P12** Extend LEGAL_PENDING.md for the new documents; advocate review remains outstanding.
* **P13** Consent-table RLS is already strong; the write surface (§4) is the real work; Cloudflare
  rules [U] — owner dashboard review.
* **P14/P15** Tests for the 16 listed scenarios; rollout report; no deploy without authorization.

## 6. Owner census / dashboard steps (answers the [U] items)

```sql
-- Run in Supabase SQL Editor (read-only):
select to_regclass('public.legal_acceptances') as acceptances_table;   -- null => 0007 not applied
select count(*) as acceptances, count(distinct user_id) as users from public.legal_acceptances;
select doc_type, version, content_sha256, published_at
  from public.legal_document_versions order by doc_type, version;      -- does transporter-terms exist?
select count(*) from public.marketing_consent_events;
select status, count(*) from public.privacy_requests group by status;
```

* Render dashboard → Environment: report `ENFORCE_LEGAL_ACCEPTANCE` (do not paste secrets).
* Render dashboard → deploys: which commit/branch produced the current backend bundle?
* Supabase dashboard → Auth: providers enabled, redirect URLs, rate limits (screenshots welcome).
* Cloudflare dashboard: rate-limit/WAF rules on login/signup/reset paths.

## 7. Decisions needed before L2 implementation

1. **transporter-terms reconciliation:** ~~reproduce the live 1.0.0 bytes into the repo (preferred —
   keeps any live acceptance valid) or publish 1.0.1; confirm whether the live DB holds the row.~~
   **Resolved 2026-11-10 — published 1.0.1 (material); see §8.** Reproducing the 1.0.0 hash proved
   impossible: the live manifest's `content_sha256` is the hash of the HTML bytes, not of markdown
   source text, and the original source is unrecoverable from the hand-written page.
2. **Enforcement architecture:** DB-layer guard (§4b) vs moving remaining writes behind the API (§4a).
3. **Flag timing:** `ENFORCE_LEGAL_ACCEPTANCE=true` only after coverage + smoke test, by the owner.
4. **Public browsing stays public** while transactional actions are gated (confirm interpretation).
5. Missing identity details (LEGAL_PENDING.md) and advocate review — unchanged, still open.

## 8. L2a resolution — transporter-terms reconciled (2026-11-10)

**Decision: publish `transporter-terms` 1.0.1 (material, effective 2026-11-09), not 1.0.0.**
Evidence that forced it: `sha256(diag/live_transporter_terms_1.0.0.html)` =
`4fff3628670504963b558262acad4549f9cd0df4afb90091d8042ff5b7ebf2d3` — **identical** to the
`content_sha256` the live manifest serves for 1.0.0. So the unknown publisher hashed the HTML
bytes, while `scripts/build_legal.py` hashes the substituted markdown text; the two methods can
never agree, and the original markdown is unrecoverable from the hand-written HTML (blank lines,
custom footer, no Questions/SHA line). Republishing 1.0.0 from the repo would therefore either
trip the immutability trigger (503 on every accept, if the live DB holds the row) or silently
replace a published hash. 1.0.1 is an INSERT, not an UPDATE: safe whether or not the live DB
holds 1.0.0, and it never touches an existing acceptance record. **[S]**

What shipped in L2a:

* `legal/source/transporter-terms.md` — all 16 sections carried over faithfully from the captured
  live page, including every "to be confirmed" marker (tax, dispute process, insurance, liability
  limits, governing-law mechanism) and both legal-review blockquotes. Contact uses `{{EMAIL}}`
  from `services/business.py` like every other document.
* Captured live artifacts preserved in-repo, byte-verified against the originals:
  `legal/archive/transporter-terms/1.0.0.live-captured.html` (9,999 bytes) and
  `1.0.0.live-captured.fragment.html` (9,066 bytes). These are evidence, not build outputs;
  `check_archive` only reads `<version>.md`, so they cannot interfere with the build.
  `.gitattributes` marks `*.live-captured.*` as `-text`: with `core.autocrlf=true` the first
  commit silently LF-normalized them (blob `28b72208…` ≠ published `4fff3628…`); a follow-up
  commit re-stored the exact bytes and verified `git show HEAD:<file>` hashes equal the
  originals (`4fff3628…` page, `233a951c…` fragment).
* Role mapping fixed in all three places that disagreed: `services/legal.py normalize_role`
  (`transporter`/`driver` → `transporter`, matching `getCurrentRole()` at index.html:9912),
  `scripts/build_legal.py ROLES`, and the SPA's `normLegalRole`. The manifest now serves
  `role_documents.transporter = [privacy-policy, terms-of-use, transporter-terms]`.
* Effect on people: anyone who accepted the live 1.0.0 keeps that record (append-only); because
  1.0.1 is material they are asked to review it once. Nobody is silently marked accepted, and no
  acceptance timestamp is rewritten. Transporters stop being shown the Buyer Terms.
* Tests: `test_each_role_sees_only_its_own_terms` now covers transporter; alias pins for
  `transporter`/`driver`; the no-public-admin-terms guard includes the role. Legal suites:
  **74 passed** (test_legal.py + test_legal_build.py). SPA: 8 inline blocks parse clean.