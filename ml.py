from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import joblib
from collections import Counter
import hashlib
import os
from pathlib import Path
import tempfile
from typing import List, Tuple

def example_counts(data):
    """Count exact labeled examples without storing their text in metadata."""
    return Counter(hashlib.sha256((label + '\0' + text).encode('utf-8')).hexdigest()
                   for label, text in data)


def pending_training_count(model, data):
    """Return added example count, or None for a legacy model."""
    trained = getattr(model, 'training_example_counts_', None)
    if trained is None:
        return None
    return sum((example_counts(data) - Counter(trained)).values())


def train_model(data: List[Tuple[str, str]], model_path: str):
    texts = [text for _, text in data]
    labels = [label for label, _ in data]
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1,2), min_df=2, max_df=0.9)),
        ("clf", LogisticRegression(solver="liblinear", class_weight="balanced"))
    ])
    pipeline.fit(texts, labels)
    pipeline.training_example_counts_ = dict(example_counts(data))
    # Replace the model only after a complete save; a failed save keeps the old one.
    target = Path(model_path)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.training-', suffix='.joblib')
    os.close(descriptor)
    try:
        joblib.dump(pipeline, temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)

def load_model(model_path: str):
    return joblib.load(model_path)

def predict(model, text: str) -> Tuple[str, float]:
    proba = model.predict_proba([text])[0]
    idx = proba.argmax()
    label = model.classes_[idx]
    confidence = proba[idx]
    return label, confidence

def evaluate_model(data: List[Tuple[str, str]], model_path: str) -> str:
    texts = [text for _, text in data]
    labels = [label for label, _ in data]
    pipeline = load_model(model_path)
    preds = pipeline.predict(texts)
    return classification_report(labels, preds)
