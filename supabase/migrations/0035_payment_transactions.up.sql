-- AgriBridge migration 0035 — Payment transactions ledger
-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- Phase 2 of the payment-system upgrade spec.
--
-- PURPOSE
-- ───────
-- The existing system tracks payment state only on the `orders` table (a single
-- payment_status column). That is enough for a simple checkout but cannot answer:
--   * Which provider processed a payment and when?
--   * Was a webhook re-played or a payment attempted more than once?
--   * What raw status did the provider report at each stage?
--   * Did a provider amount match what the buyer owed?
--
-- This migration introduces two new tables:
--
--   payment_transactions  — one row per payment attempt (a buyer hitting
--                           "pay" once = one row, even if the attempt fails).
--                           Idempotent: provider_ref must be unique per provider.
--
--   payment_events        — append-only audit log of every status transition for
--                           a transaction (provider webhook callbacks, manual
--                           admin actions, polling results, …).
--
-- app.py's _mark_checkout_paid() will be updated (Phase 4) to write here.
-- The /api/pay/history endpoint already works via the orders table proxy and
-- will switch to this table once it is live.
--
-- SAFETY
-- ──────
-- * Additive only: no existing tables are modified.
-- * All objects use IF NOT EXISTS guards — safe to re-run.
-- * payment_events is append-only (UPDATE/DELETE triggers mirror ledger_lines).
-- * Rollback: 0035_payment_transactions.down.sql
--
-- APPLY
-- ─────
-- Do NOT apply automatically. Wait for explicit user approval.
-- Then: Supabase SQL editor → paste this file → Run.

begin;

-- ── payment_transactions ─────────────────────────────────────────────────────
-- One row per payment attempt. A single order_ref may have multiple rows if the
-- buyer retried (each attempt is a new row). The latest row with status='paid'
-- is the authoritative confirmation.
--
-- Columns:
--   id                — internal PK
--   order_ref         — the checkout reference (links to orders.payment_ref)
--   buyer_id          — auth.uid() of the payer; never null for real payments
--   provider          — 'pesapal' | 'flutterwave' | 'africas_talking' | 'manual' | 'cash' | 'bank'
--   provider_ref      — provider's own transaction id (unique per provider;
--                       null only for cash/bank before confirmation)
--   method            — 'momo' | 'airtel' | 'card' | 'pesapal' | 'cash' | 'bank'
--   amount            — amount the provider reported (or buyer owed at initiation)
--   currency          — 'UGX' (single-currency for now)
--   status            — current state: initiated → processing → paid | failed | cancelled
--   provider_status   — raw status string from the last provider response
--   provider_amount   — amount provider confirmed (may differ from `amount` on mismatch)
--   ledger_journal_id — FK to ledger_journals once the payment is posted
--   initiated_at      — when the buyer triggered the payment
--   confirmed_at      — when the provider reported success (null until paid)
--   metadata          — provider-specific raw payload (IPN body, etc.)
create table if not exists public.payment_transactions (
  id                uuid        primary key default gen_random_uuid(),
  order_ref         text        not null,
  buyer_id          uuid        references auth.users(id) on delete set null,
  provider          text        not null,
  provider_ref      text,
  method            text        not null,
  amount            numeric(18,2) not null,
  currency          text        not null default 'UGX',
  status            text        not null default 'initiated',
  provider_status   text,
  provider_amount   numeric(18,2),
  ledger_journal_id uuid        references public.ledger_journals(id) on delete set null,
  initiated_at      timestamptz not null default now(),
  confirmed_at      timestamptz,
  metadata          jsonb       not null default '{}'::jsonb,

  -- Idempotency: a provider_ref must be unique within a provider.
  -- Cash/bank confirmations may share provider='manual' + null provider_ref
  -- until the admin supplies a bank ref, so the unique index is partial.
  constraint payment_transactions_provider_check
    check (provider in ('pesapal','flutterwave','africas_talking','manual','cash','bank')),
  constraint payment_transactions_method_check
    check (method in ('momo','airtel','card','pesapal','cash','bank')),
  constraint payment_transactions_status_check
    check (status in ('initiated','processing','paid','failed','cancelled','refunded')),
  constraint payment_transactions_amount_positive
    check (amount > 0)
);

-- Unique provider reference per provider (partial: only when provider_ref is not null)
create unique index if not exists uq_payment_transactions_provider_ref
  on public.payment_transactions (provider, provider_ref)
  where provider_ref is not null;

-- Fast lookup by order ref (most queries will start here)
create index if not exists idx_payment_transactions_order_ref
  on public.payment_transactions (order_ref, initiated_at desc);

-- Fast lookup by buyer (for /api/pay/history)
create index if not exists idx_payment_transactions_buyer
  on public.payment_transactions (buyer_id, initiated_at desc);

-- ── payment_events ───────────────────────────────────────────────────────────
-- Append-only log of every status transition or notable moment for a transaction.
-- Written by: provider webhooks, the status poller, manual admin actions, refunds.
--
-- Columns:
--   transaction_id — FK to payment_transactions
--   event_type     — 'initiated' | 'webhook_received' | 'status_polled' |
--                    'confirmed' | 'failed' | 'cancelled' | 'refunded' |
--                    'amount_mismatch' | 'admin_override' | 'reversal'
--   actor          — auth.uid() of the person who triggered this (null for webhooks)
--   raw_payload    — full provider payload or admin notes at this moment
--   new_status     — the status value this event moved the transaction to
--   created_at     — when the event was recorded
create table if not exists public.payment_events (
  id             bigint      generated always as identity primary key,
  transaction_id uuid        not null references public.payment_transactions(id) on delete cascade,
  event_type     text        not null,
  actor          uuid        references auth.users(id) on delete set null,
  raw_payload    jsonb       not null default '{}'::jsonb,
  new_status     text,
  created_at     timestamptz not null default now(),

  constraint payment_events_type_check
    check (event_type in (
      'initiated','webhook_received','status_polled','confirmed',
      'failed','cancelled','refunded','amount_mismatch','admin_override','reversal'
    )),
  constraint payment_events_status_check
    check (new_status is null or new_status in (
      'initiated','processing','paid','failed','cancelled','refunded'
    ))
);

create index if not exists idx_payment_events_transaction
  on public.payment_events (transaction_id, created_at desc);

-- ── Append-only guard on payment_events ─────────────────────────────────────
-- Mirrors ledger_lines: no updates or deletes once written.
create or replace function public.payment_events_block_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'payment_events is append-only: % is not allowed; add a new event instead',
    tg_op using errcode = '55000';
end $$;

drop trigger if exists payment_events_no_update_delete on public.payment_events;
create trigger payment_events_no_update_delete
  before update or delete on public.payment_events
  for each row execute function public.payment_events_block_mutation();

drop trigger if exists payment_events_no_truncate on public.payment_events;
create trigger payment_events_no_truncate
  before truncate on public.payment_events
  for each statement execute function public.payment_events_block_mutation();

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- payment_transactions:
--   * service_role: full access (backend writes, webhooks)
--   * authenticated buyer: read own rows only (for /api/pay/history)
--   * admins: read all rows
--   * anon: no access
--
-- payment_events:
--   * service_role: full access
--   * authenticated buyer: read events for own transactions
--   * admins: read all
--   * anon: no access

alter table public.payment_transactions enable row level security;
alter table public.payment_events       enable row level security;

-- Service-role full access
drop policy if exists payment_transactions_service on public.payment_transactions;
create policy payment_transactions_service on public.payment_transactions
  for all to service_role using (true) with check (true);

drop policy if exists payment_events_service on public.payment_events;
create policy payment_events_service on public.payment_events
  for all to service_role using (true) with check (true);

-- Buyer reads own transactions
drop policy if exists payment_transactions_buyer_read on public.payment_transactions;
create policy payment_transactions_buyer_read on public.payment_transactions
  for select to authenticated
  using (buyer_id = auth.uid());

-- Buyer reads events for own transactions
drop policy if exists payment_events_buyer_read on public.payment_events;
create policy payment_events_buyer_read on public.payment_events
  for select to authenticated
  using (
    exists (
      select 1 from public.payment_transactions t
      where t.id = payment_events.transaction_id
        and t.buyer_id = auth.uid()
    )
  );

-- Admin reads all
drop policy if exists payment_transactions_admin_read on public.payment_transactions;
create policy payment_transactions_admin_read on public.payment_transactions
  for select to authenticated
  using (public.is_admin(auth.uid()));

drop policy if exists payment_events_admin_read on public.payment_events;
create policy payment_events_admin_read on public.payment_events
  for select to authenticated
  using (public.is_admin(auth.uid()));

-- Lock down grants
revoke all on public.payment_transactions from anon;
revoke all on public.payment_events       from anon;

grant select on public.payment_transactions to authenticated;
grant select on public.payment_events       to authenticated;
grant all    on public.payment_transactions to service_role;
grant all    on public.payment_events       to service_role;
grant usage  on sequence public.payment_events_id_seq to service_role;

commit;
