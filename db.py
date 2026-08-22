"""
SQLite database for TTS Web.
Stores user accounts (password hashed) and generation history.
"""
import os
import sqlite3
import hashlib
import secrets
import uuid
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", "/app/output")
DB_PATH = os.path.join(DATA_DIR, "tts.db")


def get_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database with default admin user"""
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            filename TEXT,
            voice TEXT NOT NULL,
            rate TEXT DEFAULT '+0%',
            volume TEXT DEFAULT '+0%',
            subtitle_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

    # Create default admin if no users exist
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        default_user = os.environ.get("ADMIN_USERNAME", "admin")
        default_pwd = os.environ.get("ADMIN_PASSWORD", "admin123")
        add_user(default_user, default_pwd)
        print(f"DB: Created default user '{default_user}'")

    conn.close()


def hash_password(password, salt=None):
    """Hash password with SHA-256 + salt"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def add_user(username, password):
    """Add a new user"""
    conn = get_db()
    pwd_hash, salt = hash_password(password)
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (username, pwd_hash, salt)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def verify_user(username, password):
    """Verify user credentials, returns True if valid"""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return False
    pwd_hash, _ = hash_password(password, row["salt"])
    return pwd_hash == row["password_hash"]


def list_users():
    """List all users (without passwords)"""
    conn = get_db()
    rows = conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [{"id": r["id"], "username": r["username"], "created_at": r["created_at"]} for r in rows]


def delete_user(username):
    """Delete a user"""
    conn = get_db()
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def add_history(task_id, username, filename, voice, rate, volume):
    """Add a generation history record"""
    conn = get_db()
    conn.execute(
        "INSERT INTO history (id, username, filename, voice, rate, volume) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, username, filename, voice, rate, volume)
    )
    conn.commit()
    conn.close()


def update_history(task_id, subtitle_count, duration_ms, status):
    """Update history record after generation"""
    conn = get_db()
    conn.execute(
        "UPDATE history SET subtitle_count = ?, duration_ms = ?, status = ? WHERE id = ?",
        (subtitle_count, duration_ms, status, task_id)
    )
    conn.commit()
    conn.close()


def list_history(username=None, limit=50):
    """List generation history"""
    conn = get_db()
    if username:
        rows = conn.execute(
            "SELECT * FROM history WHERE username = ? ORDER BY created_at DESC LIMIT ?",
            (username, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
