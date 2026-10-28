-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0023 — the one write that decides whether a delivery is evidenced.
--
-- 0020:111-127 added the proof columns; 0021 created the private bucket; this
-- migration adds the function that compares a recipient code and records the
-- photograph. It exists as a function rather than three PostgREST calls because
-- two claims in it must be decided under the same row lock:
--
--   * the attempt counter. A 6-digit code is guessable by the very person who
--     benefits from guessing it — the driver holding the goods. Counting wrong
--     guesses in a read-then-write outside the database would let simultaneous
--     requests each see "attempts = 3" and each write "attempts = 4", so the cap
--     would never be reached. The row is locked FOR UPDATE and the counter is
--     incremented in place.
--   * "already submitted". Only the first successful submission counts; a second
--     one would silently overwrite the evidence photograph.
--
-- The caller (backend, service role) hashes the submitted code with SHA-256 and
-- passes the HEX digest. The plaintext code and the stored hash never meet: the
-- backend compares digests here, and app.py never selects proof_code_hash into a
-- response. Doing the digest in Python keeps this function free of a pgcrypto
-- dependency, which this project has not installed.
--
-- A wrong guess is NOT discarded. The photograph is stored and
-- proof_code_matches is set false, because an admin reviewing a disputed job
-- needs to see what was submitted, not just that it failed.
--
-- Re-runnable (create or replace). Adds no rows, drops no data.

begin;

create or replace function public.submit_delivery_proof(
  p_delivery_id    uuid,
  p_transporter_id uuid,
  p_code_hash      text,
  p_photo_path     text
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  r public.deliveries%rowtype;
  matched boolean;
begin
  select * into r from public.deliveries where id = p_delivery_id for update;
  if not found then
    return jsonb_build_object('ok', false, 'reason', 'delivery_not_found');
  end if;

  -- The id came from the caller's session lookup, never from the request body,
  -- but this function is the last gate: it refuses a row that is not theirs even
  -- if a future caller passes the wrong id.
  if r.transporter_id is distinct from p_transporter_id then
    return jsonb_build_object('ok', false, 'reason', 'not_yours');
  end if;

  if r.status in ('delivered', 'failed') then
    return jsonb_build_object('ok', false, 'reason', 'closed', 'status', r.status);
  end if;

  if r.proof_code_hash is null then
    return jsonb_build_object('ok', false, 'reason', 'code_not_issued');
  end if;

  if r.proof_code_matches is true then
    return jsonb_build_object('ok', true, 'reason', 'already_submitted',
                              'code_matched', true);
  end if;

  if r.proof_code_attempts >= 5 then
    return jsonb_build_object('ok', false, 'reason', 'locked_out',
                              'attempts', r.proof_code_attempts);
  end if;

  matched := (p_code_hash is not null and p_code_hash = r.proof_code_hash);

  update public.deliveries
     set proof_photo_path  = p_photo_path,
         proof_submitted_at = now(),
         proof_code_matches = matched,
         proof_code_attempts = r.proof_code_attempts + case when matched then 0 else 1 end,
         updated_at = now()
   where id = p_delivery_id;

  return jsonb_build_object(
    'ok', true,
    'code_matched', matched,
    'attempts_remaining', case when matched then 5 - r.proof_code_attempts
                               else greatest(5 - (r.proof_code_attempts + 1), 0) end,
    'submitted_at', now()
  );
end;
$$;

revoke all on function public.submit_delivery_proof(uuid, uuid, text, text)
  from public, anon, authenticated;
-- Same reason as 0020:215-216 — the revoke also removes PUBLIC's default
-- EXECUTE, so the backend that owns the call needs its grant stated explicitly.
grant execute on function public.submit_delivery_proof(uuid, uuid, text, text)
  to service_role;

commit;
