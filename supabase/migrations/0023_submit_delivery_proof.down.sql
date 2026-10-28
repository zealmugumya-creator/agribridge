-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0023 rollback — removes the proof-submission function only.
--
-- It deliberately leaves the columns and any recorded evidence in place: a
-- rollback removes the write path, not the proof that a delivery happened.
-- Dropping evidence tables/columns after the feature has been used would destroy
-- data the admin panel relies on to arbitrate a dispute. 0020's rollback owns
-- the columns, and it warns in its own header about what dropping them costs.
--
-- Safe to re-run.

begin;

drop function if exists public.submit_delivery_proof(uuid, uuid, text, text);

commit;
