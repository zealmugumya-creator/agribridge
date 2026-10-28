-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0021 — Proof-of-delivery photographs get a bucket nobody can guess a URL for.
--
-- 0020 gave a delivery a proof_photo_path column and explicitly did NOT store a
-- URL, because the only bucket this project has is 'uploads' and 0012_uploads_bucket
-- .up.sql:9-16 creates it with public = true, then :29-33 adds
--   create policy uploads_public_read on storage.objects for select to public
--     using (bucket_id = 'uploads')
-- Anything placed there is world-readable to anyone who has the path, and a
-- delivery photograph shows a real person's home, vehicle and goods. The audit
-- (docs/TRANSPORTER_PORTAL_AUDIT.md §6) lists this as a reason proof of delivery
-- could not simply reuse the existing upload path.
--
-- Design:
--
-- 1. public = false, and NO policy at all on storage.objects for this bucket.
--    RLS denies by default, so a signed-in farmer, buyer or transporter reading
--    storage.objects gets nothing. 0012's uploads_public_read is scoped to
--    bucket_id = 'uploads' and a grep across supabase/ confirms it is the only
--    storage.objects policy in this project, so the new bucket inherits no read
--    path. That was verified, not assumed.
--
-- 2. The backend writes with the service-role key and hands out short-lived
--    signed URLs from an endpoint that first checks the caller is the assigned
--    transporter or an admin. This is the same pattern 0012 already uses for
--    listing media ("Browser clients do NOT upload directly"), so no new
--    privilege is introduced and no client ever holds a storage write policy.
--    Deciding that here rather than writing an ownership-matching policy on
--    storage.objects keeps the authorization rule in one place: a policy would
--    have to parse the object name to reach deliveries.transporter_id, which is
--    fragile and would still not stop a client from listing the bucket.
--
-- 3. Photographs only, capped at 8 MiB. 0012 allows 26 MiB and video because a
--    farmer may attach harvest video. Proof of delivery has no reason to accept
--    either, and every type removed is a type that cannot be abused.
--
-- Inserts one bucket row and drops legacy policies of the same name. It reads no
-- existing table and touches no rows elsewhere. Safe to re-run.

begin;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'proof_of_delivery',
  'proof_of_delivery',
  false,
  8388608, -- 8 MiB: a phone photograph of a delivered crate, nothing else
  array['image/jpeg', 'image/png', 'image/webp']::text[]
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- A re-run must also clear any client policy that appeared for this bucket
-- between runs, including one an operator added by hand in the dashboard.
drop policy if exists proof_select on storage.objects;
drop policy if exists proof_insert on storage.objects;
drop policy if exists proof_update on storage.objects;
drop policy if exists proof_delete on storage.objects;
drop policy if exists proof_public_read on storage.objects;

commit;
