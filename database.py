import sqlite3
from contextlib import closing
from pathlib import Path
from typing import List, Tuple

def init_db(path: str):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL CHECK(label IN ('spam', 'ham')),
            text TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            chat_id INTEGER,
            message_id INTEGER,
            action TEXT,
            text TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.executescript((Path(__file__).parent / 'migrations/002_moderation_decisions.sql').read_text(encoding='utf-8'))
    if 'dry_run' not in {row[1] for row in c.execute('PRAGMA table_info(moderation_decisions)')}:
        c.execute('ALTER TABLE moderation_decisions ADD COLUMN dry_run INTEGER NOT NULL DEFAULT 0')
    conn.commit()
    conn.close()

def add_message(path: str, label: str, text: str):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("INSERT INTO messages (label, text) VALUES (?, ?)", (label, text))
    conn.commit()
    conn.close()

def get_all_messages(path: str) -> List[Tuple[str, str]]:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("SELECT label, text FROM messages")
    data = c.fetchall()
    conn.close()
    return data

def log_action(path: str, user_id: int, username: str, chat_id: int, message_id: int, action: str, text: str):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO logs (user_id, username, chat_id, message_id, action, text) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, chat_id, message_id, action, text)
    )
    conn.commit()
    conn.close()


def log_decision(path: str, record: dict, *, chat_id: int, message_id: int, source: str):
    values = {**record, 'chat_id': chat_id, 'message_id': message_id, 'source': source}
    columns = ('decision_id', 'created_at', 'chat_id', 'message_id', 'source', 'label',
        'confidence', 'p_spam', 'action', 'reason', 'delete_threshold', 'moderate_threshold',
        'model_version', 'input_sha256', 'policy_version', 'schema_version', 'dry_run')
    with closing(sqlite3.connect(path)) as conn:
        with conn:
            conn.execute('INSERT INTO moderation_decisions (' + ','.join(columns)
                + ') VALUES (' + ','.join('?' for _ in columns) + ')',
                tuple(values[k] for k in columns))
