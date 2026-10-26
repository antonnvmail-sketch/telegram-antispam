CREATE TABLE IF NOT EXISTS shadow_predictions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL, username TEXT NOT NULL DEFAULT '', message_id INTEGER NOT NULL,
 message_type TEXT NOT NULL, source_kind TEXT NOT NULL, text TEXT NOT NULL, text_hash TEXT NOT NULL, text_length INTEGER NOT NULL,
 has_url INTEGER NOT NULL DEFAULT 0, has_username INTEGER NOT NULL DEFAULT 0, has_caption INTEGER NOT NULL DEFAULT 0, is_short_message INTEGER NOT NULL DEFAULT 0,
 production_skipped INTEGER NOT NULL DEFAULT 0, production_label TEXT, production_p_spam REAL, production_action TEXT NOT NULL,
 shadow_label TEXT, shadow_p_spam REAL, shadow_hypothetical_action TEXT, models_agree INTEGER, comparison_type TEXT NOT NULL, probability_delta REAL,
 shadow_model_version TEXT NOT NULL, error TEXT, human_label TEXT CHECK(human_label IS NULL OR human_label IN ('spam','ham','exclude')), human_labeled_at DATETIME, human_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_predictions_created_at ON shadow_predictions(created_at);
CREATE INDEX IF NOT EXISTS idx_shadow_predictions_comparison ON shadow_predictions(comparison_type);
CREATE INDEX IF NOT EXISTS idx_shadow_predictions_text_hash ON shadow_predictions(text_hash);
