-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0024 rollback — restores the undeclared column scope base-schema.sql implies.
--
-- This re-opens a hole on purpose, because a rollback should return the database
-- to the state it documents, not to the state someone would prefer. Once run, a
-- signed-in farmer can PATCH `is_verified`, `is_premium`, `otp_code`, `email` and
-- `id` on their own row again through /rest/v1/farmers, and the "Verified farmer"
-- badge the buyer market sorts by becomes self-declared once more. If the intent
-- is "undo 0024 but keep people from grading themselves", do not run this — narrow
-- the column list in the up migration instead.
--
-- Safe to re-run.

begin;

grant update on public.farmers to authenticated;

commit;
