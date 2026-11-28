-- AgriBridge migration 0035 — rollback
-- Removes the payment_transactions and payment_events tables and their triggers.
-- Safe to run only when no production data has been written to these tables.
begin;

drop trigger  if exists payment_events_no_truncate         on public.payment_events;
drop trigger  if exists payment_events_no_update_delete    on public.payment_events;
drop function if exists public.payment_events_block_mutation();
drop table    if exists public.payment_events;
drop table    if exists public.payment_transactions;

commit;
