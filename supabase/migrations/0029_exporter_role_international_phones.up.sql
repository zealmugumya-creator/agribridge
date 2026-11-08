-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0029 — an honest account type for a foreign trading company, and phones that
--        can come from anywhere.
--
-- WHY THIS EXISTS. On 2026-11-06 a real offer reached the owner: Five Trading &
-- Supplies (Egypt, info@five-eg.com, +20 103 679 7717) wants to sell Egyptian red
-- and golden onions through AgriBridge. The owner's decision: fix the app first,
-- then onboard them — with a new "Exporter / Trading Co." account type rather
-- than filing a foreign company under 'farmer' or 'vendor'. Measured in this
-- repo, neither is possible today:
--
--   * Every phone rule is Ugandan. 0025 installs <table>_phone_uganda
--     ('^[+]256[37][0-9]{8}$') on farmers, buyers and transporters; the SPA
--     normalises to +256 before signUp; update_user_profile() in app.py refuses
--     anything else. An Egyptian number cannot be stored, so the company cannot
--     register with the number it actually owns.
--   * user_roles.business_role (0028:127-130) allows farmer | vendor | hotel |
--     supplier | transporter. 'exporter' is refused by the CHECK, and
--     normalize_role (services/legal.py) folds an unknown word to 'vendor',
--     which would show a seller the buyer's terms.
--
-- WHAT THIS MIGRATION DOES
--   1. Widens user_roles_business_role_chk to add 'exporter'. The column and the
--      one-role trigger stay exactly as 0028 left them: an exporter is a SELLER,
--      so their profile row lives in `farmers` (listings.farmer_id points there,
--      base-schema:38) and the trigger still refuses them a second row in buyers
--      or transporters. What makes them an exporter instead of a farmer is the
--      server-written business_role, which the backend records BEFORE the
--      farmers row exists — a farmers row on its own reads as 'farmer'
--      (_profile_account_type in app.py), and choose_account_type() locks the
--      choice once a profile row is held.
--   2. Replaces the farmers and buyers phone rules with international E.164
--      ('^[+][1-9][0-9]{6,14}$'), NOT VALID. Ugandan numbers keep working
--      (+256771234567 satisfies it); +201036797717 and +254700123456 now do too.
--      Transporters are NOT touched: drivers are created by the admin console
--      for local deliveries and keep 0025's +256 rule.
--   3. Replaces update_my_profile (0008) so the RPC validates the same E.164
--      shape and the same name blacklist as 0025's table checks. No code calls
--      this RPC today (the profile route writes REST), but leaving it
--      Ugandan-only would leave a second, contradictory rule one call away.
--
-- WHAT IT DELIBERATELY DOES NOT DO
--   * It does not re-check existing rows (every new CHECK is NOT VALID), so the
--      eight '+10000000000' placeholder accounts and any buyer with a foreign
--      number are untouched. A NOT VALID check still fires on UPDATE of an
--      existing row: a placeholder account keeps failing any edit until its
--      phone is fixed — the same pressure 0025 named, now under a rule the
--      placeholder actually satisfies, so those accounts become editable again
--      while new writes of the placeholder are refused one layer up, in
--      normalize_phone (app.py) and normalizePhoneE164 (static/index.html).
--      E.164 shape alone cannot tell +10000000000 from a real North American
--      number; the API is the write path every signup uses, so that is where
--      the known placeholder is refused.
--   * It does not touch farmers_phone_unique or its live
--      WHERE phone <> '+10000000000' predicate. One phone = one account keeps
--      applying to every real number, foreign ones included.
--   * It does not create any account. Onboarding Five Trading & Supplies is a
--      separate, owner-authorized step (Supabase invite or their own signup)
--      after this file is applied.
--
-- ORDER. Apply 0026, then 0028, then this file. The pre-flight below refuses to
-- run without 0028's column, so a skipped step is an error, not a half-guard.
--
-- Re-running is safe: constraints are drop-then-add, the function is CREATE OR
-- REPLACE. No rows are read or changed.

begin;

-- ── 1. Pre-flight ────────────────────────────────────────────────────────────
do $$
declare
  v_bypass boolean;
  v_column boolean;
begin
  select rolbypassrls into v_bypass from pg_roles where rolname = current_user;
  if v_bypass is distinct from true then
    raise exception '0029 must be applied by a role that bypasses row level security (in Supabase: postgres). Applying it as "%" would own an RPC that reads through its own policies.',
      current_user
      using errcode = '55000',
            hint   = 'Run this file in the Supabase SQL Editor signed in as the project owner. Nothing has been committed.';
  end if;

  select exists (select 1 from information_schema.columns
                  where table_schema = 'public' and table_name = 'user_roles'
                    and column_name = 'business_role') into v_column;
  if not v_column then
    raise exception '0029 needs user_roles.business_role, which migration 0028 adds'
      using errcode = '55000',
            hint   = 'Apply 0026, then 0028, then 0029. Nothing has been committed.';
  end if;
end $$;

-- ── 2. 'exporter' becomes a word the store may hold ─────────────────────────
alter table public.user_roles
  drop constraint if exists user_roles_business_role_chk;
alter table public.user_roles
  add constraint user_roles_business_role_chk
    check (business_role in ('farmer', 'vendor', 'hotel', 'supplier',
                             'transporter', 'exporter')) not valid;

comment on column public.user_roles.business_role is
  'Which account type AgriBridge treats this person as: '
  'farmer | vendor | hotel | supplier | transporter | exporter — the values '
  'normalize_role (services/legal.py) and the signup form use. One row per '
  'person is enforced by the user_id primary key (0002:29). Written by the '
  'backend at a person''s first account-type choice, or by an admin. Never read '
  'from GoTrue user_metadata, which the signed-in person can rewrite.';

-- ── 3. Phones from anywhere, on the two marketplace profile tables ──────────
-- Bracket escapes ([+], [0-9]) as in 0025: the same rule, survives a paste tool
-- that eats backslashes. farmers and buyers only — transporters keep +256.
do $$
declare
  t text;
begin
  foreach t in array array['farmers', 'buyers'] loop
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_phone_format');
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_phone_uganda');
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_phone_e164');
    execute format(
      'alter table public.%I add constraint %I check (%s) not valid',
      t, t || '_phone_e164',
      'phone is not null and phone ~ ''^[+][1-9][0-9]{6,14}$''');
  end loop;

  if not exists (select 1 from information_schema.check_constraints
                  where constraint_schema = 'public'
                    and constraint_name = 'farmers_phone_e164') then
    raise exception '0029 installed no farmers phone rule'
      using hint = 'public.farmers is missing phone, so the live table is not the '
                   'one this migration describes. Nothing else has been committed.';
  end if;
end $$;

-- ── 4. The RPC agrees with the table ────────────────────────────────────────
create or replace function public.update_my_profile(
  p_full_name text default null,
  p_phone     text default null,
  p_district  text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $fn$
declare
  v_uid uuid := auth.uid();
  v_name text;
  v_phone text;
begin
  if v_uid is null then
    raise exception 'not authenticated' using errcode = '42501';
  end if;

  if p_full_name is not null then
    v_name := trim(p_full_name);
    if length(v_name) < 2 then
      raise exception 'Full name must be at least 2 characters' using errcode = '22023';
    end if;
    -- Same list as 0025's <table>_full_name_real and update_user_profile in app.py.
    if lower(v_name) in ('test','user','admin','farmer','buyer','name','null',
                         'undefined','none','n/a','unknown user','unknown') then
      raise exception 'Please enter your real full name' using errcode = '22023';
    end if;
  end if;

  if p_phone is not null then
    v_phone := trim(p_phone);
    if v_phone !~ '^[+][1-9][0-9]{6,14}$' then
      raise exception 'Enter a valid phone number with country code (e.g. +256771234567 or +201036797717)'
        using errcode = '22023';
    end if;
    if exists (
      select 1 from public.farmers where phone = v_phone and id <> v_uid
    ) then
      raise exception 'That phone number is already registered to another account' using errcode = '23505';
    end if;
  end if;

  insert into public.farmers (id, full_name, phone, district)
    values (v_uid,
            coalesce(v_name, (select full_name from public.farmers where id = v_uid)),
            coalesce(v_phone, (select phone    from public.farmers where id = v_uid)),
            coalesce(p_district, (select district from public.farmers where id = v_uid)))
    on conflict (id) do update
      set full_name = excluded.full_name,
          phone     = excluded.phone,
          district  = excluded.district,
          updated_at = now();

  return jsonb_build_object('ok', true);
end;
$fn$;

revoke all on function public.update_my_profile(text, text, text) from public;
grant execute on function public.update_my_profile(text, text, text) to authenticated;

commit;