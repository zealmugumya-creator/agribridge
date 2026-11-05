-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0028 down — remove the one-role guard.
--
-- What this restores, and what it deliberately does not:
--   * The triggers and the `user_roles.business_role` column go. Reading no rows
--     and writing no rows, so nothing is lost: a person who had a business_role
--     set falls back to `user_metadata.role`, which is where app.py:6071 reads it
--     today. That is the pre-0028 behaviour, not a new one.
--   * `authenticated` gets whole-table INSERT back on farmers and buyers, because
--     that is the state 0026:98-114 left. This REOPENS the gap 0028 §4 closes — a
--     person can create their own row with is_verified or is_premium set — and that
--     is stated here rather than discovered later. Roll back knowing you are
--     rolling back that too.
--   * The grants on user_roles are untouched, as they were in 0026: 0025:211 revoked
--     `authenticated`'s writes there and this stage never added any, so there is
--     nothing here to take away again.

begin;

drop trigger if exists "one_role_per_person" on public.farmers;
drop trigger if exists "one_role_per_person" on public.buyers;
drop trigger if exists "one_role_per_person" on public.transporters;

drop function if exists public.reject_second_role_row();

alter table public.user_roles
  drop column if exists business_role;

grant insert on public.farmers to authenticated;
grant insert on public.buyers  to authenticated;

commit;
