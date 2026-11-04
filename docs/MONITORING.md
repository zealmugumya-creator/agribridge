# Monitoring: Supabase -> Discord + Admin

How new activity reaches you. Two paths:

1. **Backend events** (orders, payments, cold storage / transport / loan requests, signups,
   sign-ins, contact messages): the Flask app sends these to Discord itself, in the same request
   that stores the row. A first visit by an anonymous browser is **recorded but not announced** —
   it has no name to attach, and an unnamed "someone arrived" is the kind of message the owner
   objected to; it shows on the admin Activity bell instead.
2. **Rows the web app writes straight to Supabase** (privacy and deletion requests, disease
   reports, vet bookings, reviews, fraud flags, ...): a Supabase Database Webhook posts each
   new row to `POST /api/hooks/supabase`, which forwards a safe summary (no phones, emails or
   tokens) to Discord and the admin **Activity** feed.

Path 2 depends on a trigger installed by hand. Path 1 ships with the app, which is why the
forms most likely to be the first thing a customer ever uses — the contact form — were moved
to it (see below).

The admin **📥 Inbox** tab shows the same tables read-only. **🗺 Logistics** lives behind the
same admin password.

## 2026-11-04: a contact message now alerts from inside the app

A grower wrote in through the site and the owner heard nothing. Verified at the time:
`sendContactMsgCore()` posted the row directly from the browser, so the only possible alert
was the path-2 trigger. If the trigger or `SUPABASE_WEBHOOK_SECRET` is absent the message
still saves, and nothing tells anyone.

`POST /api/contact` (app.py) now stores the row and alerts in one request: it validates the
name, a reachable phone or email, and the message, then sends Discord + `system_events`
through `_safe_fields`, so no phone, email or token leaves the database. It is anonymous by
design — a visitor who has not signed up must still be able to ask — so it is rate limited to
10 per 5 minutes per IP. A failed save returns 502, and the form falls back to its old direct
insert on status 0, 404, 405 or 5xx so a backend that is down or mid-deploy cannot swallow a
message; if neither path worked the form says so instead of saying "saved".

**Remove `contact_messages` from the trigger list** (the SQL below already does). With the
trigger installed as well, every message alerts twice — the app and the webhook both fire.
If you ran the older version of this file, drop just that one:

```sql
drop trigger if exists ab_discord_hook on public.contact_messages;
```

`contact_messages` stays in `_WATCHED` in app.py, which is what lets the admin Inbox read it.

## 2026-11-04: the name in `#signups`

Every email/password account arrived as `Name —`. Google onboarding stores
`user_metadata.full_name`, `doRegister()` stored `user_metadata.name`, and the Discord embed
read the first key with a dash default. Signup now writes both keys, and `_identity_name()` in
app.py is the single answer for every backend surface: metadata `full_name`, then metadata
`name`, then the `farmers` profile row, then the words "not given at sign-up". A name that was
never given is now stated as missing instead of rendered as a dash that reads like a database
fault. Forcing a real name at sign-up is stage N5 and needs a migration; until then this only
makes the absence visible.

### What is verified and what is not

Verified in production on 2026-11-04: the deployed SPA at agribrige.com contains the new
form (221,417 bytes fetched, `/api/contact` present, "Saved locally" gone), and the live
backend answers `POST /api/contact` with `{"error":"Please give the name we should reply
to.","ok":false}` — the route exists and refuses before writing anything. CI was green on
`e81fe08` (517 tests) and the deploy run hash-checked the published pages.

**Not verified:** that a real message reaches Discord. That needs one genuine contact
submission, which also needs `DISCORD_WEBHOOK_SUPPORT` (or the catch-all) set in Render.
Send one from the site and check `#support` — if it does not arrive, the Discord webhook
variable is the first thing to look at, not the code.

## 2026-11-04: who is on the site — the Activity feed and its bell

The owner's question was "who entered AgriBridge, and what did they do". Three parts, each
measured against what was actually in the code before this:

- **Every event now names the person who sent it.** The events were already being written from
  about 60 places, but only eight passed a `user_id`, so the table could not answer "what did
  this person do" about anything else. `_request_uid()` resolves the caller from their own
  bearer token through GoTrue — an id claimed in a request body is not believed — caches it on
  `g` so a page load costs one auth round trip rather than one per event, and is skipped for
  admin JWTs, which GoTrue would only 401 after a trip Render cannot spare.
- **Arrival and sign-in are recorded.** `POST /api/activity` writes `activity.visit.first` once
  per browser (the SPA sends it from its existing first-visit flag; a signed-in visitor is
  attributed, an anonymous one is left anonymous rather than identified from an IP). Sign-in is
  recorded where the backend can actually see it — `/api/legal/sync-signup`, which every session
  start calls — as `session_started` with the verified name, role and provider, and pings
  `#signups` at most **once a day per person** (`session_alerted` is the marker). That endpoint
  runs on every reload, so an unthrottled alert would bury the channel. The ping carries no
  email and no phone.
- **The admin console says when something is new.** The Activity tab carries a count of the
  `severity=info` events newer than the last time it was opened — `GET /api/admin/events?severity=info&since=…`,
  once a minute, only while the tab is visible, stopped on sign-out: Render runs two sync
  workers and a console left open in a pocket should not spend them.
  Opening the tab is what marks the feed seen; the marker lives in `localStorage`
  (`ab_admin_activity_seen`). Clicking a person re-reads the feed as
  `GET /api/admin/events?user_id=<uuid>` — their own timeline.

What is deliberately **not** recorded: presses. The whitelist in `_ACTIVITY_EVENTS` is one event
long and anything else is refused with a 400 before a row is written. A feed of clicks would
drown the timeline that answers the actual question, put a POST in front of a farmer on every
keystroke, and grow `system_events` without end — there is no retention rule on that table yet.
Per-press telemetry is a different product decision, not a missing feature.

`user_id` and `since` are validated rather than passed through: a malformed id is a 400, not a
filter that quietly matches nothing, because an empty timeline would read as "this person never
did anything" and cost the owner attention for nothing.

### What is verified and what is not

Verified live on 2026-11-04 after `598f000`: the published `admin.html` is byte-identical to the
repository file (133,163 bytes, sha256 `f141ac3c54a7d081`, LF-normalised — Cloudflare serves the
clean URL, so `/admin.html` answers 307 → `/admin`), the published SPA is byte-identical
(874,928 bytes, sha256 `b50e107d3be5bf00`) and contains the `visit.first` beacon, the live
backend answers `POST /api/activity` with `{"error":"unknown event","ok":false}` for a press and
401 `Missing token` for `/api/admin/events`. CI was green on `598f000` — all three check-runs
(`Backend lint + type + tests`, `Migration 0007 on real PostgreSQL (PGlite)`, `deploy`)
concluded `success`; the number of tests CI ran is not readable here, because `gh` is not
authenticated on this machine and the job logs are not public through the checks API. Running
CI's own command locally (`pytest -q`) gives 603 passed, 7 of them new browser tests that drive
the real `admin.html` in headless Chromium.

**Not verified:** that a real arrival appears in the feed. `activity.visit.first` needs a browser
with no `ab_visited` flag to hit the site, and reading the feed back needs the admin password,
which is not held here. Open **Activity** after a fresh visit: the badge should move and the row
should appear. **Not verified either:** that the daily `#signups` sign-in ping arrives, which
needs `DISCORD_WEBHOOK_SIGNUPS` (or the catch-all `DISCORD_WEBHOOK_URL`) set in Render — the code
path is tested, delivery is not.

## Render environment variables

Render dashboard -> your service (agribridge-1) -> **Environment** -> **Add Environment Variable**.
Save, then Render redeploys.

| Variable | Where to get it |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord channel -> gear icon (Edit Channel) -> Integrations -> Webhooks -> New Webhook -> **Copy Webhook URL**. This is the catch-all channel. |
| `DISCORD_WEBHOOK_ORDERS` | Optional. Same steps in an #orders channel (orders + payments). |
| `DISCORD_WEBHOOK_SIGNUPS` | Optional. #signups channel. |
| `DISCORD_WEBHOOK_LISTINGS` | Optional. #listings channel (listings, scans, reviews, community). |
| `DISCORD_WEBHOOK_MATCHING` | Optional. #matching channel. |
| `DISCORD_WEBHOOK_TRANSPORT` | Optional. #transport channel. |
| `DISCORD_WEBHOOK_REQUESTS` | Optional. #requests channel (cold storage, cold transport, loans, vet, purchase). |
| `DISCORD_WEBHOOK_SUPPORT` | Optional. #support channel (contact, privacy, deletion, disease, fraud flags). |
| `DISCORD_WEBHOOK_ERRORS` | Optional. #errors channel (server errors, AI). |
| `SUPABASE_WEBHOOK_SECRET` | You make it up: any long random string. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"`. Use the same value in the SQL below. |
| `SUPABASE_URL`, `SUPABASE_KEY` | Supabase -> Project Settings -> API: Project URL and the **service_role** key (keep secret). |
| `ADMIN_PASSWORD`, `JWT_SECRET` | You choose them. Long and random. |
| `PUBLIC_BASE_URL` | Your site, e.g. `https://agribrige.com` (no trailing slash). |
| `FLW_SECRET_KEY` | Flutterwave dashboard -> Settings -> API Keys -> Secret key. |
| `FLW_WEBHOOK_HASH` | Flutterwave dashboard -> Settings -> Webhooks -> "Secret hash" (you pick it). Set the webhook URL to `https://agribridge-1-og7a.onrender.com/api/pay/webhook/flutterwave`. |
| `SENTRY_DSN` | Optional. sentry.io -> Project -> Settings -> Client Keys (DSN). |

Channels you leave unset fall back to `DISCORD_WEBHOOK_URL`.

After saving, log in to admin and open **📥 Inbox -> Send Discord test**. Each configured channel
should get a test message.

## Supabase: send new rows to the backend

Supabase dashboard -> **SQL Editor** -> New query. Replace `PASTE_SAME_SECRET_AS_RENDER`, then Run.
(Do not commit this file with the real secret in it.)

```sql
do $$
declare
  t text;
  hook_url text := 'https://agribridge-1-og7a.onrender.com/api/hooks/supabase';
  hook_headers text := '{"Content-Type":"application/json","X-Webhook-Secret":"PASTE_SAME_SECRET_AS_RENDER"}';
begin
  foreach t in array array[
    'account_deletion_requests','privacy_requests','disease_reports',
    'vet_bookings','purchase_requests','supply_orders','payouts','fraud_flags',
    'admin_devices','user_roles','reviews','community_posts','transporters',
    'farmers','buyers','order_offers'
  ] loop
    if to_regclass('public.' || t) is not null then
      execute format('drop trigger if exists ab_discord_hook on public.%I', t);
      execute format(
        'create trigger ab_discord_hook after insert on public.%I for each row ' ||
        'execute function supabase_functions.http_request(%L, %L, %L, %L, %L)',
        t, hook_url, 'POST', hook_headers, '{}', '5000');
    end if;
  end loop;
end $$;
```

If it errors with `schema "supabase_functions" does not exist`, enable it first:
Database -> Extensions -> turn on **pg_net**, then run again.

To remove the hooks later:
`drop trigger if exists ab_discord_hook on public.<table>;`
