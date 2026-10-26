"""Create a local demo database and model without importing the bot or contacting Telegram."""
import argparse
from contextlib import closing
from collections import Counter
from pathlib import Path
import sqlite3

from database import init_db
from import_dataset import read_dataset
from ml import train_model


def prepare(data_path, database_path, model_path):
    data_path, database_path, model_path = map(Path, (data_path, database_path, model_path))
    if database_path.resolve() == model_path.resolve():
        raise ValueError('Database and model paths must differ')
    if database_path.exists() or model_path.exists():
        raise ValueError('Refusing to overwrite an existing database or model; choose new paths')
    rows = read_dataset(data_path)
    counts = Counter(label for label, _ in rows)
    if set(counts) != {'spam', 'ham'}:
        raise ValueError('Training requires both spam and ham examples')
    if not database_path.parent.is_dir() or not model_path.parent.is_dir():
        raise ValueError('Output directories must already exist')
    try:
        # A failed fit does not leave an empty database masquerading as a ready demo.
        train_model(rows, str(model_path))
        init_db(str(database_path))
        with closing(sqlite3.connect(database_path)) as conn, conn:
            conn.executemany('INSERT INTO messages (label, text) VALUES (?, ?)', rows)
    except Exception:
        database_path.unlink(missing_ok=True)
        model_path.unlink(missing_ok=True)
        raise
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data.example.txt')
    parser.add_argument('--db', default='dataset.sqlite')
    parser.add_argument('--model', default='model.joblib')
    args = parser.parse_args()
    try:
        counts = prepare(args.data, args.db, args.model)
    except (OSError, ValueError) as error:
        parser.exit(2, f'Demo preparation failed: {error}\n')
    print(f'Demo ready: {counts}. Synthetic examples only; no quality claim. No Telegram calls.')


if __name__ == '__main__':
    main()
