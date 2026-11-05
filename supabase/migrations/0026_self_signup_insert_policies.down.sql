-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0026 rollback — removes the INSERT policies, leaves the revokes alone.
--
-- Dropping the three policies returns the database to the state the 2026-11-05
-- census found, in which no signed-in client can create a profile row at all and
-- signup therefore leaves people with an auth account and no profile. That is the
-- state this migration was written to end, so a rollback genuinely re-breaks
-- self-service signup. It is here because every migration in this directory has a
-- down file and because the alternative — a rollback that cannot be reviewed — is
-- worse than a rollback that is honest about what it costs.
--
-- What is NOT re-granted: anon's insert/update/delete/truncate/references/trigger
-- on farmers, buyers, transporters and user_roles. The census measured those
-- privileges on farmers, buyers and user_roles, but transporters is 0014's work,
-- and from here the two cannot be told apart — a rollback that guessed would
-- quietly undo a lockdown somebody else installed while believing it was undoing
-- 0026. Leaving them revoked is less permissive than the pre-0026 state, which is
-- the safe direction to be wrong in. If a rollback really needs anon writes back,
-- grant them explicitly, per table, after reading pg_class.relacl.
--
-- No rows are touched. Safe to re-run.

begin;

drop policy if exists "farmers_owner_insert" on public.farmers;
drop policy if exists "buyers_owner_insert" on public.buyers;
drop policy if exists "transporters_owner_insert" on public.transporters;

commit;
