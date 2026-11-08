-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- Rollback for 0030. Restores the rules as 0028 and 0025 left them:
--   * business_role without 'exporter' (NOT VALID, so a row already written as
--     exporter is not scanned and does not abort the rollback — it simply
--     cannot be written again until 0030 is re-applied);
--   * the +256 phone rule on farmers and buyers. Any row saved with a foreign
--     number while 0030 was live keeps that number but fails its next UPDATE,
--     the same NOT VALID pressure 0025 documented in reverse;
--   * 0008's update_my_profile, Ugandan rule and original blacklist.
-- No rows are read or changed.

begin;

alter table public.user_roles
  drop constraint if exists user_roles_business_role_chk;
alter table public.user_roles
  add constraint user_roles_business_role_chk
    check (business_role in ('farmer', 'vendor', 'hotel', 'supplier', 'transporter'))
    not valid;

comment on column public.user_roles.business_role is
  'Which account type AgriBridge treats this person as: '
  'farmer | vendor | hotel | supplier | transporter — the values normalize_role '
  '(services/legal.py:41) and the signup form already use. One row per person is '
  'already enforced by the user_id primary key (0002:29). Written by the backend at '
  'a person''s first account-type choice, or by an admin. Never read from GoTrue '
  'user_metadata, which the signed-in person can rewrite.';

do $$
declare
  t text;
begin
  foreach t in array array['farmers', 'buyers'] loop
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_phone_e164');
    execute format('alter table public.%I drop constraint if exists %I', t, t || '_phone_uganda');
    execute format(
      'alter table public.%I add constraint %I check (%s) not valid',
      t, t || '_phone_uganda',
      'phone is not null and phone ~ ''^[+]256[37][0-9]{8}$''');
  end loop;
end $$;

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
    if lower(v_name) in ('test','user','admin','farmer','buyer','name','null','undefined','none','n/a') then
      raise exception 'Please enter your real full name' using errcode = '22023';
    end if;
  end if;

  if p_phone is not null then
    v_phone := trim(p_phone);
    if v_phone !~ '^[+]256[37][0-9]{8}$' then
      raise exception 'Enter a valid Ugandan phone number (e.g. +256771234567)' using errcode = '22023';
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