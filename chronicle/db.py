"""Хранилище летописца: одна SQLite-база и папка для отчётов.

Где лежит всё: CHRONICLE_HOME (по умолчанию ~/.local/share/claude-chronicle).
"""

import os
import sqlite3
import time
from pathlib import Path

HOME = Path(os.environ.get("CHRONICLE_HOME") or Path.home() / ".local" / "share" / "claude-chronicle").expanduser()
DB_PATH = HOME / "chronicle.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS errors (ts REAL NOT NULL, session TEXT, error TEXT NOT NULL);
"""


class ChronicleError(Exception):
    pass


def connect() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.executescript(SCHEMA)
    return con


def error(con: sqlite3.Connection, session: str | None, text: str) -> None:
    con.execute("INSERT INTO errors (ts, session, error) VALUES (?,?,?)", (time.time(), session, text[:500]))


def meta_get(con: sqlite3.Connection, k: str) -> str | None:
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else None


def meta_set(con: sqlite3.Connection, k: str, v: str) -> None:
    con.execute("INSERT INTO meta (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 90:
        return f"{s} с назад"
    if s < 5400:
        return f"{s // 60} мин назад"
    if s < 172800:
        return f"{s // 3600} ч назад"
    return f"{s // 86400} дн назад"
