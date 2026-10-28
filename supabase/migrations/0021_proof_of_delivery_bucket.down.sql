-- Copyright (C) 2026 zealmugumya-creator. All rights reserved.
--
-- 0021 rollback — removes the proof_of_delivery bucket.
--
-- This refuses to run rather than deleting evidence. storage.objects has an
-- ON DELETE CASCADE foreign key to storage.buckets in Supabase's own schema, so
-- `delete from storage.buckets` erases every object row it holds, and the
-- matching files are removed with it. Those rows are proof that a delivery
-- arrived: once a payout has been confirmed against them, destroying them takes
-- away the only record that a dispute could be settled from. So the rollback
-- counts first and raises if anything is stored; an operator who genuinely
-- wants the bucket gone exports the objects, then re-runs this.
--
-- If 0020 has also been rolled back, deliveries.proof_photo_path is gone while
-- the objects may remain — that orphan case is exactly why this guard is not
-- simply "drop the bucket if the column exists".
--
-- Safe to re-run.

begin;

drop policy if exists proof_select on storage.objects;
drop policy if exists proof_insert on storage.objects;
drop policy if exists proof_update on storage.objects;
drop policy if exists proof_delete on storage.objects;
drop policy if exists proof_public_read on storage.objects;

do $$
declare
  n bigint;
begin
  select count(*) into n from storage.objects where bucket_id = 'proof_of_delivery';
  if n > 0 then
    raise exception
      'refusing to drop proof_of_delivery: it still holds % stored object(s). '
      'Deleting the bucket cascades to those rows and their files, which are the '
      'evidence behind confirmed deliveries. Export them first, then re-run.', n;
  end if;
  delete from storage.buckets where id = 'proof_of_delivery';
end $$;

commit;
