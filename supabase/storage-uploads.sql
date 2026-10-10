-- Storage policies for the public `uploads` bucket (listing photos/videos).
-- Create the bucket first (Storage -> New bucket -> name: uploads, Public: on), then run this.
-- Stricter than the old project: anonymous visitors cannot upload or overwrite files.
create policy uploads_select on storage.objects for select to anon, authenticated
  using (bucket_id = 'uploads');
create policy uploads_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'uploads');
create policy uploads_update on storage.objects for update to authenticated
  using (bucket_id = 'uploads' and owner_id = (select auth.uid())::text)
  with check (bucket_id = 'uploads' and owner_id = (select auth.uid())::text);
