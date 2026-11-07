ALTER TABLE contact_messages
  DROP COLUMN IF EXISTS replied_at,
  DROP COLUMN IF EXISTS replied_by,
  DROP COLUMN IF EXISTS reply_text;
