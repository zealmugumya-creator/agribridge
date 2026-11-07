-- Migration 0029: add reply tracking columns to contact_messages
-- Admin replies are stored here so the inbox shows "replied" status.
-- All columns are nullable — existing rows stay untouched.

ALTER TABLE contact_messages
  ADD COLUMN IF NOT EXISTS replied_at   timestamptz,
  ADD COLUMN IF NOT EXISTS replied_by   text,
  ADD COLUMN IF NOT EXISTS reply_text   text;

COMMENT ON COLUMN contact_messages.replied_at  IS 'UTC timestamp when an admin sent a reply';
COMMENT ON COLUMN contact_messages.replied_by  IS 'Admin user sub/email who sent the reply';
COMMENT ON COLUMN contact_messages.reply_text  IS 'Last reply text sent to the sender';
