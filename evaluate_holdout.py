"""Evaluate a trusted frozen model on separate labeled text; never train or call Telegram."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import unicodedata

from import_dataset import read_dataset
from ml import load_model, predict


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def check_separation(training, holdout):
    if not training or {label for label, _ in holdout} != {'spam', 'ham'}:
        raise ValueError('Provide training rows and a holdout with both spam and ham')
    train_texts = {normalized(text) for _, text in training}
    test_texts = [normalized(text) for _, text in holdout]
    if len(set(test_texts)) != len(test_texts):
        raise ValueError('Holdout contains duplicate normalized messages')
    if train_texts.intersection(test_texts):
        raise ValueError('Holdout overlaps training data; no independent score produced')


def evaluate(model, rows, delete_threshold=0.7, moderate_threshold=0.4):
    if not 0 <= moderate_threshold <= delete_threshold <= 1:
        raise ValueError('Thresholds must satisfy 0 <= moderate <= delete <= 1')
    decisions = Counter()
    confusion = {label: {'spam': 0, 'ham': 0} for label in ('spam', 'ham')}
    for actual, text in rows:
        label, confidence = predict(model, text)
        if label not in {'spam', 'ham'} or not 0 <= confidence <= 1:
            raise ValueError('Invalid model result')
        confusion[actual][label] += 1
        action = ('delete' if label == 'spam' and confidence > delete_threshold else
                  'notify' if label == 'spam' and confidence > moderate_threshold else 'allow')
        decisions[(actual, action)] += 1
    totals = Counter(label for label, _ in rows)
    deleted_spam, deleted_ham = decisions[('spam', 'delete')], decisions[('ham', 'delete')]
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    return {'samples': dict(totals), 'classifier_confusion_actual_then_predicted': confusion,
            'policy_decisions': {label: {action: decisions[(label, action)] for action in ('allow', 'notify', 'delete')}
                                 for label in ('spam', 'ham')},
            'deletion_precision': ratio(deleted_spam, deleted_spam + deleted_ham),
            'spam_deletion_recall': ratio(deleted_spam, totals['spam']),
            'ham_false_deletion_rate': ratio(deleted_ham, totals['ham']),
            'thresholds': {'delete': delete_threshold, 'moderate': moderate_threshold}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, help='Only a trusted local joblib model')
    parser.add_argument('--training-data', required=True, help='Actual labeled training TXT for this model')
    parser.add_argument('--data', required=True, help='Separate labeled holdout TXT; never used for training')
    parser.add_argument('--delete-threshold', type=float, default=0.7)
    parser.add_argument('--moderate-threshold', type=float, default=0.4)
    args = parser.parse_args()
    try:
        training, holdout = read_dataset(args.training_data), read_dataset(args.data)
        check_separation(training, holdout)
        report = evaluate(load_model(args.model), holdout, args.delete_threshold, args.moderate_threshold)
        report['sha256'] = {key: hashlib.sha256(Path(value).read_bytes()).hexdigest()
                            for key, value in {'model': args.model, 'training': args.training_data, 'holdout': args.data}.items()}
        report['limitations'] = ('No normalized exact overlap with supplied training file; similar templates and '
                                 'the true model training history are not verified. Results apply only to this labeled holdout.')
    except (OSError, ValueError):
        # No paths, message text or model exception internals in terminal/logs.
        parser.exit(2, 'Evaluation failed: check trusted model, labeled files, classes, overlap and thresholds.\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
