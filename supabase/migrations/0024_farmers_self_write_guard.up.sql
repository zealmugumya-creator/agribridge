-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0024 — A person may edit their own profile. They may not grade it.
--
-- supabase/base-schema.sql:257 declares
--   create policy farmers_owner_update on public.farmers
--     for update to authenticated using (auth.uid() = id) with check (auth.uid() = id)
-- and Supabase's default privileges hand `authenticated` UPDATE on every public
-- table. using/with_check decide WHICH ROWS a signed-in farmer may touch, never
-- WHICH COLUMNS — the same defect 0019 closed on orders and 0022 closed on
-- transporters, so a direct PostgREST PATCH against their own row reaches:
--
--   is_verified      false -> true    the badge buyers sort and filter on
--                                     (static/index.html:3638, :4472) and the
--                                     profile page prints as "Verified farmer"
--                                     beside the sentence "Checked against your
--                                     ID and farm by the AgriBridge team"
--                                     (static/index.html:7798).
--   is_premium       false -> true    labelled "Set by the AgriBridge team"
--                                     (static/index.html:7799) while remaining
--                                     writable by the person it advertises for.
--   otp_code         any text         base-schema.sql:285 revokes READ of this
--                                     column from anon/authenticated, which is
--                                     the right instinct applied to the wrong
--                                     verb: nothing stops the row's owner
--                                     WRITING a code that a future verification
--                                     step will then compare against. Same for
--                                     otp_expires_at.
--   email, id        any value        desynchronises the row from the verified
--                                     auth account behind it.
--
-- 0008_enforce_profile_name_phone.up.sql already guards the identity FIELDS —
-- full_name length, the +256 phone pattern, one account per phone, and the
-- update_my_profile() RPC that refuses 'test'/'user'/'admin' as names. That is
-- why POST /api/user/profile can reject a junk name today. What no layer of this
-- project does today is stop a farmer from declaring themselves verified, and a
-- claim a buyer can sort listings by is a claim with money attached to it.
--
-- Nothing in the web app writes the columns this takes away. The client writes
-- left in static/index.html are profile_photo (:7850, :8495) and
-- preferred_language (:12502) — both stay on the grant list below. The name,
-- phone and district edit used to be a third one, a direct PATCH at the old :8507;
-- it now goes through POST /api/user/profile, which applies the same rules as
-- sign-up and reports the answer. The admin console reaches
-- is_verified/is_premium through /api/admin/*, which uses the service-role key
-- and bypasses RLS and these grants unchanged. So this migration removes
-- privileges the app does not use and keeps the ones it does.
--
-- The column list is resolved against information_schema at run time, because
-- `preferred_language` is not in base-schema.sql:6-21 — it is either a live
-- dashboard column or absent — and a grant on a column that does not exist would
-- abort the whole migration. Unknown columns are skipped; a farmers table with
-- none of the expected profile columns aborts instead, since that means the live
-- schema is not the one this file describes.
--
-- Reads are untouched, so buyers still see farmer profiles
-- (farmers_public_select). Re-running is safe: revoke then grant is idempotent.
--
-- Verified in source, not probed in production: the policy text and the client
-- call sites above are read from the files, and no live project query was run
-- while writing this. Applying it to the project is the owner's step, and only
-- the owner can see whether the live grants match what base-schema.sql declares.
--
-- No rows are touched.

begin;

do $$
declare
  wanted text[] := array['full_name', 'phone', 'district', 'profile_photo',
                         'sub_county', 'village', 'farm_size_acres', 'crops_grown',
                         'preferred_language'];
  cols   text;
begin
  select string_agg(quote_ident(c.column_name), ', ' order by c.ordinal_position)
    into cols
    from information_schema.columns c
   where c.table_schema = 'public'
     and c.table_name   = 'farmers'
     and c.column_name  = any (wanted);

  if cols is null then
    raise exception
      '0024 found none of the profile columns it expects on public.farmers'
      using hint = 'The live table does not match supabase/base-schema.sql:6-21. '
                   'Compare the real schema before applying, and do not run the '
                   'statement on its own — the revoke below it has not run either.';
  end if;

  execute 'revoke update on public.farmers from public, anon, authenticated';
  execute format('grant update (%s) on public.farmers to authenticated', cols);

  raise notice '0024: authenticated may now write %', replace(cols, '"', '');
end $$;

commit;
