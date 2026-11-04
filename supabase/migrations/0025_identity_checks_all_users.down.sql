-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0025 rollback — returns the database to the state the 2026-11-04 census found.
--
-- This re-opens two holes on purpose, because a rollback restores the state a
-- migration documented, not the state someone would prefer:
--
--   1. The farmers phone rule goes back to the generic international check
--      '^\+[1-9]\d{6,14}$' (the live definition the census read out of
--      pg_constraint), which accepts +10000000000 and any other foreign-format
--      number. The +256 rule is dropped, not left dangling.
--   2. anon and authenticated get their write privileges on farmers, buyers,
--      transporters and user_roles back, including whole-table UPDATE on farmers
--      — so a signed-in farmer can again PATCH is_verified, is_premium, otp_code,
--      email and id on their own row, and an unauthenticated caller is again
--      limited only by whatever RLS happens to be enabled on those tables.
--
-- The name rules are dropped for all three tables, since 0025 added them and
-- they did not exist before it.
--
-- If the intent is "undo 0025 but keep people from grading themselves or
-- registering with a US test number", do not run this — narrow the up migration
-- instead.
--
-- No rows are touched. Safe to re-run.

begin;

-- ── 1. Drop the rules 0025 installed ────────────────────────────────────────
do $$
declare
  t text;
begin
  foreach t in array array['farmers','buyers','transporters'] loop
    if exists (select 1 from information_schema.tables
                where table_schema = 'public' and table_name = t) then
      execute format('alter table public.%I drop constraint if exists %I', t,
                     t || '_full_name_real');
      execute format('alter table public.%I drop constraint if exists %I', t,
                     t || '_phone_uganda');
    end if;
  end loop;
end $$;

-- Restore the farmers phone rule the census measured, and only if farmers still
-- has a phone column. It is re-added NOT VALID for the same reason 0025 added its
-- replacement that way: existing rows must not be re-checked by a rollback.
do $$
begin
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'farmers'
                and column_name = 'phone') then
    execute 'alter table public.farmers drop constraint if exists farmers_phone_format';
    execute 'alter table public.farmers add constraint farmers_phone_format '
            || 'check (phone ~ ''^[+][1-9][0-9]{6,14}$'') not valid';
  end if;
end $$;

-- ── 2. Re-open the privileges ───────────────────────────────────────────────
grant insert, update, delete, truncate, references, trigger
  on public.farmers, public.buyers, public.transporters, public.user_roles
  to anon, authenticated;

grant update on public.farmers to authenticated;

commit;
