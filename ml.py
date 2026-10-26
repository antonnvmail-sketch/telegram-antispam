from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
import joblib
from typing import List, Tuple

def train_model(data: List[Tuple[str, str]], model_path: str):
    texts = [text for _, text in data]
    labels = [label for label, _ in data]
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1,2), min_df=2, max_df=0.9)),
        ("clf", LogisticRegression(solver="liblinear", class_weight="balanced"))
    ])
    pipeline.fit(texts, labels)
    joblib.dump(pipeline, model_path)

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
