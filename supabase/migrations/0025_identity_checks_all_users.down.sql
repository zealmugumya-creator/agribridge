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
--   2. authenticated gets whole-table UPDATE on farmers back — so a signed-in
--      farmer can again PATCH is_verified, is_premium, otp_code, email and id on
--      their own row, which is the hole 0024 and 0025 both close.
--
-- anon is not touched, in either direction. The up migration revokes nothing from
-- anon, because the production write path currently runs as anon (see the grants
-- bullet in the up file), so a rollback that re-granted anon writes would be
-- handing out a privilege that was never taken away — and on transporters it would
-- also undo 0014.
--
-- The authenticated writes 0025 revoked on buyers, transporters and user_roles are
-- deliberately NOT re-granted. Nothing between 0014 and 0025 can be told apart
-- from here — all three migrations speak about the same role on the same tables —
-- and a rollback that guessed wrong would silently re-open a lockdown someone
-- else installed. A rollback that leaves those three closed is less permissive
-- than the pre-0025 state and is the safe direction to be wrong in. If a rollback
-- genuinely needs them open, grant them explicitly after reading the census.
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

-- ── 2. Re-open the farmers column scope for authenticated ───────────────────
-- Removing a column-level grant is not the same as never having one: the
-- whole-table UPDATE has to come back for the state to match what the census
-- found, and then the narrow grant 0025 installed has to go, or the two overlap
-- and the rollback appears to have done nothing.
grant update on public.farmers to authenticated;

do $$
declare cols text;
begin
  select string_agg(quote_ident(a.attname), ', ' order by a.attnum)
    into cols
    from pg_attribute a
   where a.attrelid = 'public.farmers'::regclass
     and a.attnum > 0
     and a.attacl is not null
     and exists (select 1 from aclexplode(a.attacl) g
                  where g.privilege_type = 'UPDATE'
                    and g.grantee = 'authenticated'::regrole);

  if cols is not null then
    execute format('revoke update (%s) on public.farmers from authenticated', cols);
    raise notice '0025 down: removed the narrow farmers column grants (%)',
                 replace(cols, '"', '');
  end if;
end $$;

commit;
