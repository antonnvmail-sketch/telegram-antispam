CREATE TABLE IF NOT EXISTS moderation_decisions (
    decision_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('filter', 'test_spam')),
    label TEXT NOT NULL CHECK(label IN ('spam', 'ham')),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    p_spam REAL NOT NULL CHECK(p_spam BETWEEN 0 AND 1),
    action TEXT NOT NULL CHECK(action IN ('allow', 'moderate', 'delete')),
    reason TEXT NOT NULL,
    delete_threshold REAL NOT NULL,
    moderate_threshold REAL NOT NULL,
    model_version TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 0 CHECK(dry_run IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_moderation_decisions_message
    ON moderation_decisions(chat_id, message_id);
