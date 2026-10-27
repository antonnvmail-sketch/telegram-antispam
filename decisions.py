"""Shared, versioned decision policy; no Telegram or application configuration."""
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import uuid

POLICY_VERSION = 'binary-thresholds-v1'


@dataclass(frozen=True)
class LoadedModel:
    pipeline: object
    version: str


def load_versioned_model(path, *, loader=None):
    # Hash and deserialize the SAME bytes, even if the path is replaced meanwhile.
    raw = Path(path).read_bytes()
    version = 'sha256:' + hashlib.sha256(raw).hexdigest()
    if loader is None:
        import joblib
        loader = joblib.load
    return LoadedModel(loader(io.BytesIO(raw)), version)


@dataclass(frozen=True)
class Decision:
    label: str
    confidence: float
    p_spam: float
    action: str
    reason: str
    delete_threshold: float
    moderate_threshold: float
    model_version: str
    input_sha256: str
    policy_version: str = POLICY_VERSION
    schema_version: int = 1
    dry_run: bool = False
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_record(self):
        return asdict(self)


def decide(label, confidence, *, delete_threshold, moderate_threshold,
           model_version, text, p_spam=None):
    confidence = float(confidence)
    delete_threshold, moderate_threshold = float(delete_threshold), float(moderate_threshold)
    if label not in {'spam', 'ham'}:
        raise ValueError('Unsupported class')
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Invalid confidence')
    if not 0 <= moderate_threshold <= delete_threshold <= 1:
        raise ValueError('Invalid thresholds')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', model_version):
        raise ValueError('Missing loaded model digest')
    inferred = confidence if label == 'spam' else 1.0 - confidence
    p_spam = inferred if p_spam is None else float(p_spam)
    if not math.isfinite(p_spam) or not 0 <= p_spam <= 1 or not math.isclose(p_spam, inferred, abs_tol=1e-9):
        raise ValueError('Inconsistent spam probability')
    # Retain the original label gate and strict > comparisons, including ties.
    if label != 'spam':
        action, reason = 'allow', 'predicted_ham'
    elif confidence > delete_threshold:
        action, reason = 'delete', 'spam_above_delete_threshold'
    elif confidence > moderate_threshold:
        action, reason = 'moderate', 'spam_above_moderate_threshold'
    else:
        action, reason = 'allow', 'spam_not_above_moderate_threshold'
    return Decision(str(label), confidence, p_spam, action, reason,
        delete_threshold, moderate_threshold, model_version,
        hashlib.sha256(text.encode('utf-8')).hexdigest())


def classify_and_decide(model, text, *, delete_threshold, moderate_threshold, dry_run=False):
    classes = list(model.pipeline.classes_)
    probabilities = model.pipeline.predict_proba([text])[0]
    values = [float(p) for p in probabilities]
    if len(classes) != 2 or set(classes) != {'ham', 'spam'} or len(values) != 2:
        raise ValueError('Expected binary ham/spam model')
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in values) or not math.isclose(sum(values), 1.0, abs_tol=1e-9):
        raise ValueError('Invalid probability vector')
    winner = max(range(len(values)), key=values.__getitem__)
    decision = decide(classes[winner], values[winner], delete_threshold=delete_threshold,
        moderate_threshold=moderate_threshold, model_version=model.version, text=text,
        p_spam=values[classes.index('spam')])
    return replace(decision, dry_run=bool(dry_run))


def persist_decision(decision, *, chat_id, message_id, source, write_record, logger):
    context = {**decision.to_record(), 'chat_id': chat_id, 'message_id': message_id,
               'source': source, 'stage': 'decision', 'status': 'selected'}
    logger.info(json.dumps(context, sort_keys=True, allow_nan=False))
    try:
        write_record(decision.to_record(), chat_id=chat_id, message_id=message_id, source=source)
    except Exception as exc:
        logger.error(json.dumps({**context, 'stage': 'decision_journal', 'status': 'failed',
            'error_type': type(exc).__name__}, sort_keys=True, allow_nan=False))
        return False
    return True


def diagnostic_text(decision, *, saved):
    recommendations = {'delete': '🗑️ Рекомендация: удалить',
                       'moderate': '⚠️ Рекомендация: передать на модерацию',
                       'allow': '✅ Рекомендация: сохранить'}
    return ('🔍 Результат анализа:\n'
        f'Классификация: {decision.label}\nP(spam): {decision.p_spam:.6f}\n'
        f'{recommendations[decision.action]}\nПричина: {decision.reason}\n'
        f'Пороги: удаление > {decision.delete_threshold}; модерация > {decision.moderate_threshold}\n'
        f'Модель: {decision.model_version}\nПолитика: {decision.policy_version}\n'
        f'Решение: {decision.decision_id}\n'
        + ('Решение сохранено в журнале.\n' if saved else '⚠️ Запись в БД не удалась; решение есть в файловом журнале.\n')
        + 'Это проверка текста. Действия не выполнялись.'
        + ('\n🧪 Включён пробный режим (DRY_RUN).' if decision.dry_run else ''))
