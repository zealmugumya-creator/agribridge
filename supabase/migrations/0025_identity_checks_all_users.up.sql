-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0025 — Identity rules for EVERY user table, not just farmers.
--
-- Measured on the live project on 2026-11-04 (owner ran the census; the numbers
-- below are what those queries returned, not what the repo declares):
--
--   farmers_phone_format  CHECK ((phone ~ '^\+[1-9]\d{6,14}$'))
--       A generic international E.164 check. It is NOT 0008's rule
--       (0008_enforce_profile_name_phone.up.sql:28 says '^\+256[37]\d{8}$'), and
--       it is the reason +10000000000 was accepted: 11 digits starting with 1
--       satisfies it. Reproduced in PGlite: '+256' is refused by it, and
--       '+10000000000' is not.
--   farmers_full_name_nonempty  ABSENT. Nothing in the database objects to an
--       empty name. Reproduced: an insert with full_name = '' succeeded.
--   farmers_phone_unique  indisvalid = true, with
--       WHERE (phone <> '+10000000000'::text)
--       The index is healthy and does enforce one-phone-per-account — for every
--       number except that one, which is exempt by a predicate that appears
--       nowhere in this repository. Reproduced in PGlite: three more placeholder
--       rows inserted without complaint (8 -> 11), while a second
--       +256771234567 was refused with 23505. The repo's own version of the
--       index (0008:31, `where phone is not null`) cannot even be built over
--       these rows: "could not create unique index ... Key (phone)=(+10000000000)
--       is duplicated." So the exemption was added to make an existing problem
--       stop raising an error, and it guarantees the problem keeps multiplying.
--   buyers / transporters / user_roles  no name check and no phone-format check
--       of any kind (buyers: buyer_type check + unique email/phone; transporters:
--       status check + unique supabase_uid). The brief's "login security should
--       be for all users" is unenforced for every user type but farmers, and even
--       farmers' rule is the wrong one.
--   grants  farmers, buyers and user_roles all still carry anon=arwdDxtm — the
--       unauthenticated role can insert, update, delete and truncate those tables
--       as far as privileges go (0014 fixed system_events and transporters only).
--       Whether a given anon request succeeds then depends entirely on RLS being
--       enabled on that table, which this migration does not measure.
--
--       This file does NOT revoke those anon privileges, because reading the code
--       that performs the writes says why: every backend write helper sends the
--       publishable key as its bearer token, so PostgREST runs them as anon —
--       app.py:395 supa_insert, :421 supa_update, :439 supa_delete and the
--       profile upsert at :5583 all build
--       'Authorization': f'Bearer {SUPABASE_KEY}', and only supa_get_admin (:325)
--       uses SUPABASE_SERVICE_KEY, for reads. In the browser, sb()
--       (static/index.html:3312) sends 'Bearer ' + (_authToken || SUPA_KEY), so a
--       signed-in SPA call is authenticated and an anonymous one is anon, and the
--       only direct write to farmers is the language PATCH at :12502, which
--       hardcodes the publishable key and so always runs as anon. Revoking anon
--       writes here would therefore not close a hole nobody uses — it would cut
--       the write path every order, listing, contact message and profile save is
--       currently on.
--
--       The hole is real and this file says so plainly: as long as the backend
--       writes as anon, the only thing between the public publishable key (it is
--       in static/index.html:3295, by design) and these tables is RLS. Fixing it
--       means moving the writes to the service-role key or to the caller's own
--       token FIRST, and revoking anon afterward — one cannot precede the other,
--       and the order is why this is 0025 and that is 0026.
--
-- WHAT THIS MIGRATION DOES
--   1. Replaces the farmers phone rule with the +256 rule and adds the name rule,
--      then adds the same pair to buyers and transporters.
--   2. Adds every constraint NOT VALID: existing rows are not re-checked, so no
--      row is touched and the migration cannot fail because of the 8 placeholder
--      accounts or of a buyer who registered with a Kenyan number. New and
--      updated rows must satisfy the rule.
--   3. For farmers, replaces authenticated's whole-table UPDATE grant with the
--      profile columns the profile screen actually writes (the 0024 move, repeated
--      here so one migration is enough), and takes authenticated's write access to
--      buyers, transporters and user_roles away, because no signed-in client code
--      writes those three tables — the SPA never names them and the backend writes
--      them as anon. Reads are untouched.
--
--      Nothing is taken from anon. See the grants bullet above: the backend is
--      anon right now, so that revoke belongs to the stage that moves the writes.
--
-- WHAT IT DELIBERATELY DOES NOT DO
--   It does not drop or rebuild farmers_phone_unique. The honest index cannot be
--   built while several accounts share one number — that is the error quoted
--   above, and it is the correct error. Cleaning those rows is a data change with
--   consequences (listings, orders, reviews and payouts point at farmer ids:
--   supabase/base-schema.sql:38, :68, :102, :111) and needs a backup and the
--   owner's decision, so it is a separate migration (0026) after the owner has
--   seen which rows are affected.
--
--   It does not enable or change RLS policies. Row visibility is a different
--   question from column writability and this file changes only privileges.
--
-- CONSEQUENCE WORTH READING BEFORE APPLYING
--   A NOT VALID check still fires on UPDATE of an existing row. So an account
--   whose stored phone is +10000000000 cannot have ANY column changed — not by
--   its owner through the profile screen, and not by an administrator flipping
--   is_verified — until that phone is fixed. That is the intended pressure, but
--   it will look like a bug to whoever hits it, and the rows it will hit are
--   countable in advance with the queries in docs/IMPLEMENTATION_STATUS.md.
--
-- Column lists are resolved against information_schema at run time, because a
-- grant on a column that does not exist aborts the whole migration and this
-- project's live schema has already proved to differ from the repo's. A table
-- that lacks both identity columns is skipped with a NOTICE; a farmers table
-- with neither the profile columns nor the identity columns aborts, since that
-- means the live schema is not the one this file describes.
--
-- Reads are untouched. Re-running is safe: every statement is drop-then-add or
-- revoke-then-grant. No rows are touched.

begin;

-- ── 1. The rules themselves ─────────────────────────────────────────────────
-- The pattern is written with bracket escapes ([+], [0-9]) rather than
-- backslashes: it means the same thing and survives being pasted through a tool
-- that eats backslashes, which is not hypothetical for this project.

do $$
declare
  t        text;
  has_name boolean;
  has_phone boolean;
begin
  foreach t in array array['farmers','buyers','transporters'] loop

    select exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = t
                      and column_name = 'full_name') into has_name;
    select exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = t
                      and column_name = 'phone') into has_phone;

    if not has_name and not has_phone then
      raise notice '0025: public.% has neither full_name nor phone — skipped', t;
      continue;
    end if;

    if has_name then
      execute format('alter table public.%I drop constraint if exists %I', t,
                     t || '_full_name_real');
      execute format(
        'alter table public.%I add constraint %I check (%s) not valid',
        t, t || '_full_name_real',
        'full_name is not null and length(trim(full_name)) >= 2 and '
        || 'lower(trim(full_name)) not in '
        || '(''test'',''user'',''admin'',''farmer'',''buyer'',''name'',''null'','
        || '''undefined'',''none'',''n/a'',''unknown user'',''unknown'')');
    end if;

    if has_phone then
      -- The live farmers rule is named farmers_phone_format; a repo-applied 0008
      -- would have named it the same, so dropping by name covers both histories.
      execute format('alter table public.%I drop constraint if exists %I', t,
                     t || '_phone_format');
      execute format('alter table public.%I drop constraint if exists %I', t,
                     t || '_phone_uganda');
      execute format(
        'alter table public.%I add constraint %I check (%s) not valid',
        t, t || '_phone_uganda',
        'phone is not null and phone ~ ''^[+]256[37][0-9]{8}$''');
    end if;

  end loop;

  if not exists (select 1 from information_schema.check_constraints
                  where constraint_schema = 'public'
                    and constraint_name in ('farmers_full_name_real',
                                            'farmers_phone_uganda')) then
    raise exception '0025 installed no farmers identity rule at all'
      using hint = 'public.farmers is missing both full_name and phone, so the '
                   'live table is not the one this migration describes. Nothing '
                   'else in this file has been committed: the transaction is '
                   'still open and will abort.';
  end if;
end $$;

-- ── 2. Write privileges, narrowed for the role the clients actually use ─────
-- anon is deliberately left alone. Every write helper in app.py authenticates to
-- PostgREST with the publishable key, so anon is the role the production write
-- path runs as today; revoking it here would break orders, listings, contact
-- messages and profile saves without removing any capability, because the same
-- key would still be the one sending them. The stage that takes anon's writes is
-- the stage that first moves those helpers onto the service-role key (0026), and
-- the test file states which privileges remain open after this one.

-- authenticated: farmers keeps the profile columns (the 0024 move, repeated so
-- this one migration is enough). buyers / transporters / user_roles lose write
-- entirely; reads are untouched, so the public browse pages, the transporter
-- portal and the admin console keep working exactly as they do today.
revoke update on public.farmers from authenticated;

do $$
declare
  wanted text[] := array['full_name', 'phone', 'district', 'profile_photo',
                         'sub_county', 'village', 'farm_size_acres', 'crops_grown',
                         'preferred_language'];
  cols text;
begin
  select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position)
    into cols
    from information_schema.columns c
   where c.table_schema = 'public' and c.table_name = 'farmers'
     and c.column_name = any (wanted);

  if cols is null then
    raise exception '0025 found none of the profile columns it expects on public.farmers'
      using hint = 'Compare the live table with supabase/base-schema.sql:6-21 before '
                   'applying. The revokes above have not been committed either.';
  end if;

  execute format('grant update (%s) on public.farmers to authenticated', cols);
  raise notice '0025: authenticated may now write % on farmers', replace(cols, '"', '');
end $$;

revoke insert, update, delete, truncate, references, trigger
  on public.buyers, public.transporters, public.user_roles
  from authenticated;

-- No select is granted here. authenticated keeps read access through the
-- whole-table SELECT that Supabase's default privileges installed, and this file
-- revokes no reads, so re-granting select would be a no-op dressed up as a fix.
-- The read side — including the otp_code column that base-schema.sql:285 tries,
-- and fails, to protect — is its own stage.

commit;
