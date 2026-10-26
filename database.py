import sqlite3
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
