-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0031 down — remove the database consent guard.
--
-- What this restores, and what it deliberately does not:
--   * The seven triggers go, so marketplace writes are once again governed only by
--     the existing RLS write policies and the Flask gate (L2c1). That REOPENS the
--     direct-PostgREST / RPC bypass this file closes — a signed-in user who has not
--     accepted the current terms can list, review, order or accept an offer through
--     PostgREST without the backend seeing it. Stated here rather than discovered
--     later: roll back knowing you are rolling that protection back too.
--   * legal_current_requirements is dropped. It holds only the synced requirement
--     set (derived from the manifest), never user data, so nothing is lost. The
--     acceptance evidence in legal_acceptances (0007) is untouched — this file never
--     wrote to it.
--   * No grant on any pre-existing table is changed: 0031 only revoked client roles
--     on the NEW requirements table (dropped here) and installed triggers. The
--     marketplace write policies from base-schema.sql are exactly as they were.
--   * The order_offers trigger is dropped only if that table exists, mirroring the
--     conditional creation in the up file, so rollback is clean on a project that
--     never applied 0004.

begin;

drop trigger if exists legal_consent_guard on public.listings;
drop trigger if exists legal_consent_guard on public.animal_listings;
drop trigger if exists legal_consent_guard on public.supplier_products;
drop trigger if exists legal_consent_guard on public.reviews;
drop trigger if exists legal_consent_guard on public.community_posts;
drop trigger if exists legal_consent_guard on public.orders;

do $$
begin
  if exists (select 1 from information_schema.tables
              where table_schema = 'public' and table_name = 'order_offers') then
    drop trigger if exists legal_consent_guard on public.order_offers;
  end if;
end $$;

drop function if exists public._enforce_legal_consent();
drop function if exists public.legal_consent_complete(uuid);
drop function if exists public._legal_ver_ge(text, text);
drop function if exists public.sync_legal_current_requirements(jsonb);

drop table if exists public.legal_current_requirements;

commit;