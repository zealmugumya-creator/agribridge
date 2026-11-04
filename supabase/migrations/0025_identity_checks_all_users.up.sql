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
--       enabled on that table, which this migration does not measure. It removes
--       the privilege so the answer no longer depends on remembering to check.
--
-- WHAT THIS MIGRATION DOES
--   1. Replaces the farmers phone rule with the +256 rule and adds the name rule,
--      then adds the same pair to buyers and transporters.
--   2. Adds every constraint NOT VALID: existing rows are not re-checked, so no
--      row is touched and the migration cannot fail because of the 8 placeholder
--      accounts or of a buyer who registered with a Kenyan number. New and
--      updated rows must satisfy the rule.
--   3. Removes write privileges the applications do not use, and for farmers
--      replaces the whole-table UPDATE grant with the profile columns the SPA and
--      the backend actually write (the 0024 move, repeated here so one migration
--      is enough).
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

-- ── 2. Write privileges the applications do not use ─────────────────────────
-- anon: no client code writes any of these tables before sign-in. The SPA's only
-- PostgREST writes are sbUpdate('farmers', …) at static/index.html:7850 and :8495
-- (profile_photo) and the direct PATCH at :12502 (preferred_language), all of
-- which run as authenticated; /api/user/profile (app.py:5525) also runs as
-- authenticated; buyers, transporters and user_roles have no client writer at all
-- (a grep of static/*.html for those table names returns only
-- static/admin.html:351, which is a label map — the admin console reads through
-- /api/admin/*, which uses the service-role key).
revoke insert, update, delete, truncate, references, trigger
  on public.farmers, public.buyers, public.transporters, public.user_roles
  from anon, public;

-- authenticated: farmers keeps the profile columns (the 0024 move, repeated so
-- this one migration is enough). buyers / transporters / user_roles lose write
-- entirely; reads are untouched, so the public browse pages, the transporter
-- portal and the admin console keep working exactly as they do today.
revoke update on public.farmers from public, anon, authenticated;

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

-- 0014 handed authenticated `select, update` on transporters and 0022 revokes the
-- update again; 0022 is still unapplied, so the select is re-granted here rather
-- than assumed. Without it the transporter portal's own account screen breaks.
grant select on public.transporters to authenticated;
grant select on public.buyers to authenticated;

commit;
