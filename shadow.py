from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
USERNAME_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b")


def normalize_for_hash(text: str) -> str:
    """Normalize only enough to group repeated observations."""
    value = unicodedata.normalize("NFKC", text)
    value = re.sub(r"[\u200B-\u200D\u2060\uFEFF]", "", value)
    return " ".join(value.casefold().split())


def text_hash(text: str) -> str:
    """Return a non-reversible normalized-text fingerprint."""
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def spam_probability(model: Any, text: str) -> tuple[str, float]:
    """Return predicted label and P(spam), independent of class ordering."""
    probabilities = model.predict_proba([text])[0]
    classes = list(model.classes_)
    spam_index = classes.index("spam")
    probability = float(probabilities[spam_index])
    predicted_index = max(range(len(probabilities)), key=probabilities.__getitem__)
    label = str(classes[predicted_index])
    return label, probability


def probability_bucket(probability: float) -> str:
    """Map a score to an analysis-only bucket."""
    if probability < 0.50:
        return "<0.50"
    if probability < 0.60:
        return "0.50-0.60"
    if probability < 0.70:
        return "0.60-0.70"
    if probability < 0.80:
        return "0.70-0.80"
    return ">=0.80"


def comparison_type(production_label: str | None, shadow_label: str | None, production_skipped: bool) -> str:
    """Classify agreement without creating a moderation decision."""
    if production_skipped:
        return "production_skipped"
    if production_label is None or shadow_label is None:
        return "comparison_unavailable"
    if production_label == shadow_label:
        return f"agree_{production_label}"
    return f"prod_{production_label}_shadow_{shadow_label}"


def admin_command_allowed(user_id: int | None, admin_ids: list[int]) -> bool:
    """Keep shadow diagnostics restricted to configured administrators."""
    return user_id is not None and user_id in admin_ids


@dataclass(slots=True)
class ShadowRuntime:
    """Fail-open owner for one loaded shadow model and its isolated writes."""

    enabled: bool
    model_path: Path
    model_version: str
    database_path: Path
    database_timeout: float = 0.25
    model: Any | None = None
    load_error: str | None = None
    runtime_error_count: int = 0
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def load(cls, *, enabled: bool, model_path: str, model_version: str, database_path: str, database_timeout: float = 0.25) -> ShadowRuntime:
        runtime = cls(enabled=enabled, model_path=Path(model_path), model_version=model_version, database_path=Path(database_path), database_timeout=database_timeout)
        if not enabled:
            logger.warning("Shadow mode is disabled")
            return runtime
        try:
            runtime.model = joblib.load(runtime.model_path)
            if "spam" not in list(runtime.model.classes_):
                raise ValueError("shadow model has no spam class")
            logger.warning("Shadow model loaded: version=%s", model_version)
        except (OSError, ValueError, TypeError, AttributeError, ImportError) as exc:
            runtime.load_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Shadow model load failed; production continues")
        return runtime

    @property
    def loaded(self) -> bool:
        return self.model is not None

    async def observe(self, *, chat_id: int, user_id: int, username: str, message_id: int, message_type: str, source_kind: str, text: str, has_caption: bool, production_label: str | None, production_p_spam: float | None, production_action: str, production_skipped: bool) -> bool:
        """Predict and persist one observation; never raise into production."""
        if not self.enabled:
            return False
        shadow_label: str | None = None
        shadow_p_spam: float | None = None
        error: str | None = self.load_error
        if self.model is not None:
            try:
                shadow_label, shadow_p_spam = await asyncio.to_thread(spam_probability, self.model, text)
            except Exception as exc:  # Shadow boundary contains arbitrary estimator failures.
                self.runtime_error_count += 1
                error = f"predict:{type(exc).__name__}: {exc}"
                logger.exception("Shadow prediction failed; production continues")
        hypothetical = probability_bucket(shadow_p_spam) if shadow_p_spam is not None else None
        comparison = comparison_type(production_label, shadow_label, production_skipped)
        agree = None if production_label is None or shadow_label is None else int(production_label == shadow_label)
        delta = None if production_p_spam is None or shadow_p_spam is None else shadow_p_spam - production_p_spam
        values = {
            "chat_id": chat_id, "user_id": user_id, "username": username, "message_id": message_id,
            "message_type": message_type, "source_kind": source_kind, "text": text, "text_hash": text_hash(text),
            "text_length": len(text), "has_url": int(bool(URL_RE.search(text))),
            "has_username": int(bool(USERNAME_RE.search(text))), "has_caption": int(has_caption),
            "is_short_message": int(len(text) <= 20 or len(text.split()) <= 3),
            "production_skipped": int(production_skipped), "production_label": production_label,
            "production_p_spam": production_p_spam, "production_action": production_action,
            "shadow_label": shadow_label, "shadow_p_spam": shadow_p_spam,
            "shadow_hypothetical_action": hypothetical, "models_agree": agree,
            "comparison_type": comparison, "probability_delta": delta,
            "shadow_model_version": self.model_version, "error": error,
        }
        try:
            async with self._write_lock:
                await asyncio.to_thread(self._insert, values)
            return True
        except Exception:  # SQLite failures must never affect Telegram handling.
            self.runtime_error_count += 1
            logger.exception("Shadow DB write failed; production continues")
            return False

    def _insert(self, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        connection = sqlite3.connect(self.database_path, timeout=self.database_timeout)
        try:
            connection.execute(f"INSERT INTO shadow_predictions ({columns}) VALUES ({placeholders})", tuple(values.values()))
            connection.commit()
        finally:
            connection.close()

    async def stats(self) -> dict[str, int | str]:
        """Return diagnostics without exposing paths or secrets."""
        if not self.enabled:
            return {"total": 0, "agree": 0, "disagree": 0, "prod_spam_shadow_ham": 0, "prod_ham_shadow_spam": 0, "db_errors": self.runtime_error_count}
        try:
            result = await asyncio.to_thread(self._read_stats)
            result["db_errors"] = self.runtime_error_count
            return result
        except Exception as exc:
            self.runtime_error_count += 1
            logger.exception("Shadow stats failed; production continues")
            return {"total": 0, "agree": 0, "disagree": 0, "prod_spam_shadow_ham": 0, "prod_ham_shadow_spam": 0, "db_errors": self.runtime_error_count, "error": type(exc).__name__}

    def _read_stats(self) -> dict[str, int]:
        connection = sqlite3.connect(self.database_path, timeout=self.database_timeout)
        try:
            row = connection.execute("""SELECT COUNT(*), COALESCE(SUM(models_agree = 1), 0), COALESCE(SUM(models_agree = 0), 0), COALESCE(SUM(comparison_type = 'prod_spam_shadow_ham'), 0), COALESCE(SUM(comparison_type = 'prod_ham_shadow_spam'), 0), COALESCE(SUM(error IS NOT NULL), 0) FROM shadow_predictions""").fetchone()
        finally:
            connection.close()
        return {"total": row[0], "agree": row[1], "disagree": row[2], "prod_spam_shadow_ham": row[3], "prod_ham_shadow_spam": row[4], "stored_errors": row[5]}
