import sqlite3
from contextlib import closing

DB_PATH = "dataset.sqlite"  # путь к вашей базе
TXT_PATH = "data.txt"       # путь к вашему txt-файлу

def read_dataset(txt_path):
    """Parse all rows before any write; do not print users' message text on errors."""
    rows = []
    with open(txt_path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith("spam "):
                label = "spam"
                text = line[5:]
            elif line.startswith("ham "):
                label = "ham"
                text = line[4:]
            else:
                raise ValueError(f"Invalid label on line {number}; expected spam or ham")
            if not text.strip():
                raise ValueError(f"Empty message on line {number}")
            rows.append((label, text))
    return rows


def import_txt_to_db(txt_path, db_path):
    from database import init_db
    rows = read_dataset(txt_path)
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executemany("INSERT INTO messages (label, text) VALUES (?, ?)", rows)
    return len(rows)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Append labeled messages to a local database")
    parser.add_argument('--data', default=TXT_PATH)
    parser.add_argument('--db', default=DB_PATH)
    args = parser.parse_args()
    print(f"Imported {import_txt_to_db(args.data, args.db)} messages")
