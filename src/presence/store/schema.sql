-- Connection PRAGMAs live in db.py: they must run outside a transaction, and
-- executescript wraps this file in one.

CREATE TABLE IF NOT EXISTS principals (
  id            TEXT PRIMARY KEY,
  display_name  TEXT,
  tz            TEXT,
  locale        TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS identities (
  surface       TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  principal_id  TEXT NOT NULL,
  display_name  TEXT,
  linked_at     TEXT NOT NULL,
  PRIMARY KEY (surface, external_id)
);

CREATE TABLE IF NOT EXISTS link_codes (
  code          TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  used_at       TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
  key           TEXT PRIMARY KEY,
  surface       TEXT NOT NULL,
  channel_id    TEXT NOT NULL,
  thread_id     TEXT,
  principal_id  TEXT,
  title         TEXT,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,
  conv_key      TEXT NOT NULL,
  role          TEXT NOT NULL,
  content_json  TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conv_key, created_at);

CREATE TABLE IF NOT EXISTS memories (
  id            TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL,
  scope         TEXT NOT NULL,          -- principal | conversation | workspace
  scope_key     TEXT,                   -- conv key when scope = conversation
  key           TEXT NOT NULL,
  value         TEXT NOT NULL,
  source_envelope_id TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_unique
  ON memories (principal_id, scope, IFNULL(scope_key, ''), key);

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  envelope_id   TEXT,
  conv_key      TEXT,
  principal_id  TEXT,
  surface       TEXT,
  provider      TEXT,
  model         TEXT,
  status        TEXT,                   -- running | done | failed | parked
  turns         INTEGER DEFAULT 0,
  pending_json  TEXT,
  error         TEXT,
  started_at    TEXT NOT NULL,
  ended_at      TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL,
  name          TEXT NOT NULL,
  args_json     TEXT,
  decision      TEXT,                   -- allow | confirm | deny
  ok            INTEGER,
  preview       TEXT,
  ms            INTEGER,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toolcalls_run ON tool_calls (run_id);

CREATE TABLE IF NOT EXISTS triggers (
  id            TEXT PRIMARY KEY,
  principal_id  TEXT NOT NULL,
  kind          TEXT NOT NULL,          -- once | cron
  cron          TEXT,
  run_at        TEXT,
  prompt        TEXT NOT NULL,
  target_conv_key TEXT,
  enabled       INTEGER DEFAULT 1,
  last_fired_at TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen (
  surface       TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  seen_at       TEXT NOT NULL,
  PRIMARY KEY (surface, external_id)
);

-- Captured leads. `business` is the tenant key: one deployment, many business
-- lines later, without a second table. Nothing here is trusted -- every field
-- was typed by a stranger or read off a photo, so the human confirms in chat
-- before the row lands.
CREATE TABLE IF NOT EXISTS leads (
  id              TEXT PRIMARY KEY,
  business        TEXT NOT NULL DEFAULT 'default',
  name            TEXT,
  phone           TEXT,
  email           TEXT,
  company         TEXT,
  interest        TEXT,
  note            TEXT,
  id_number       TEXT,                 -- read off an uploaded ID, never invented
  source_surface  TEXT,
  source_conv_key TEXT,
  principal_id    TEXT,
  attachment_kind TEXT,                 -- image | file, when one was uploaded
  attachment      BLOB,
  status          TEXT NOT NULL DEFAULT 'new',
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_business ON leads (business, created_at);
-- The email address is unique: one lead per person, because the same person
-- talking twice used to land as two rows. That index is created in db.py
-- rather than here -- this file runs on every connection, so a database that
-- predates the constraint has to warn instead of failing to open. See
-- _ensure_unique_lead_email.

-- Files people send, held against the conversation until something claims them.
-- A photo arrives on one message and the lead is written on a later one (after
-- the confirmation button), by which point the original envelope is long gone,
-- so the bytes have to outlive the turn that carried them.
CREATE TABLE IF NOT EXISTS uploads (
  id            TEXT PRIMARY KEY,
  conv_key      TEXT NOT NULL,
  kind          TEXT NOT NULL,          -- image | file | audio
  mime          TEXT,
  data          BLOB NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploads_conv ON uploads (conv_key, created_at);
