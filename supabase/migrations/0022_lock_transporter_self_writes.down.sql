-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0022 rollback — re-creates exactly what 0014_lockdown_internal_tables declared.
--
-- This restores a hole on purpose, because a rollback should return the database
-- to the state it documents, not to the state someone would prefer. Once run, a
-- signed-in transporter can PATCH `status`, `is_available`, `vehicle_type`,
-- `capacity_kg` and `supabase_uid` on their own row again, which re-breaks the
-- inactive-transporter refusal in 0020's claim_delivery(). If the intent is
-- "undo 0022 but keep drivers from reactivating themselves", do not run this —
-- add a narrower policy instead.
--
-- Safe to re-run.

begin;

drop policy if exists "transporter update own availability" on public.transporters;
create policy "transporter update own availability" on public.transporters
  for update to authenticated using (supabase_uid = auth.uid())
  with check (supabase_uid = auth.uid());

grant update on public.transporters to authenticated;

commit;
