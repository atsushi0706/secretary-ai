"""SQLite による永続化レイヤー。

会話ログ・会話から抽出したタスク・通知履歴を1つのDBで管理。

スキーマ:
  conversations    秘書と本人のやり取り（朝/夜モード・日付・テキスト）
  extracted_tasks  会話/メール/Zoomから抽出してGoogleタスクに入れた履歴
  notifications    LINE等で送った通知の履歴(重複送信防止と検証用)
  briefings        朝夜ブリーフィングのMarkdown本文(後でLINE送信に再利用)
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "secretary.db"

JST = dt.timezone(dt.timedelta(hours=9))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_date_mode ON conversations(date, mode);

CREATE TABLE IF NOT EXISTS extracted_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    mode            TEXT NOT NULL,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    notes           TEXT,
    due             TEXT,
    urgency         TEXT,
    importance      TEXT,
    time_label      TEXT,
    google_task_id  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ext_date ON extracted_tasks(date, mode);

CREATE TABLE IF NOT EXISTS briefings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(date, mode)
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,
    type        TEXT NOT NULL,
    body        TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    success     INTEGER NOT NULL DEFAULT 1,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_sent ON notifications(sent_at);

CREATE TABLE IF NOT EXISTS quickmemo (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    body        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return dt.datetime.now(JST).isoformat(timespec="seconds")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(_SCHEMA)


# ── 会話ログ ────────────────────────────────────────────────
def save_message(date: str, mode: str, role: str, content: str) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO conversations(date, mode, role, content, created_at)"
            " VALUES(?,?,?,?,?)",
            (date, mode, role, content, _now_iso()),
        )
        return cur.lastrowid


def load_messages(date: str, mode: str) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT role, content FROM conversations"
            " WHERE date=? AND mode=? ORDER BY id ASC",
            (date, mode),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def list_conversation_days(limit: int = 30) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT date, mode, COUNT(*) AS n FROM conversations"
            " GROUP BY date, mode ORDER BY date DESC, mode DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 抽出タスク履歴 ────────────────────────────────────────────
def save_extracted_task(
    *, date: str, mode: str, source: str, title: str, notes: str = "",
    due: str | None = None, urgency: str | None = None,
    importance: str | None = None, time_label: str | None = None,
    google_task_id: str | None = None, status: str = "pending",
) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO extracted_tasks(date, mode, source, title, notes, due,"
            " urgency, importance, time_label, google_task_id, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, mode, source, title, notes, due, urgency, importance,
             time_label, google_task_id, status, _now_iso()),
        )
        return cur.lastrowid


def mark_task_pushed(extracted_id: int, google_task_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE extracted_tasks SET google_task_id=?, status='pushed'"
            " WHERE id=?",
            (google_task_id, extracted_id),
        )


def list_extracted_tasks(date: str, mode: str | None = None) -> list[dict]:
    sql = "SELECT * FROM extracted_tasks WHERE date=?"
    args: list = [date]
    if mode:
        sql += " AND mode=?"
        args.append(mode)
    sql += " ORDER BY id ASC"
    with _conn() as con:
        return [dict(r) for r in con.execute(sql, args).fetchall()]


# ── ブリーフィング（朝/夜の本文。LINE Push に再利用） ───────────
def save_briefing(date: str, mode: str, body: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO briefings(date, mode, body, created_at) VALUES(?,?,?,?)"
            " ON CONFLICT(date, mode) DO UPDATE SET body=excluded.body,"
            " created_at=excluded.created_at",
            (date, mode, body, _now_iso()),
        )


def load_briefing(date: str, mode: str) -> str | None:
    with _conn() as con:
        row = con.execute(
            "SELECT body FROM briefings WHERE date=? AND mode=?",
            (date, mode),
        ).fetchone()
    return row["body"] if row else None


# ── 通知履歴 ──────────────────────────────────────────────
def log_notification(channel: str, type_: str, body: str,
                     success: bool = True, error: str = "") -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO notifications(channel, type, body, sent_at, success, error)"
            " VALUES(?,?,?,?,?,?)",
            (channel, type_, body, _now_iso(), 1 if success else 0, error),
        )
        return cur.lastrowid


def already_notified(channel: str, type_: str, date: str) -> bool:
    """同日同種類の通知が成功済みなら True(二重送信防止)。"""
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM notifications WHERE channel=? AND type=?"
            " AND sent_at LIKE ? AND success=1 LIMIT 1",
            (channel, type_, f"{date}%"),
        ).fetchone()
    return bool(row)


# ── クイックメモ（サイドバー用・1行レコード） ──────────────────
def load_quickmemo() -> str:
    with _conn() as con:
        row = con.execute("SELECT body FROM quickmemo WHERE id=1").fetchone()
    return row["body"] if row else ""


def save_quickmemo(body: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO quickmemo(id, body, updated_at) VALUES(1, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET body=excluded.body,"
            " updated_at=excluded.updated_at",
            (body, _now_iso()),
        )


# ── ユーティリティ ─────────────────────────────────────────
def export_day(date: str) -> dict:
    """その日の会話・抽出タスク・ブリーフィングをまとめて返す(検証用)。"""
    with _conn() as con:
        conv = con.execute(
            "SELECT mode, role, content, created_at FROM conversations"
            " WHERE date=? ORDER BY id", (date,),
        ).fetchall()
        ext = con.execute(
            "SELECT * FROM extracted_tasks WHERE date=?", (date,),
        ).fetchall()
        brf = con.execute(
            "SELECT mode, body FROM briefings WHERE date=?", (date,),
        ).fetchall()
    return {
        "date": date,
        "conversations": [dict(r) for r in conv],
        "extracted_tasks": [dict(r) for r in ext],
        "briefings": [dict(r) for r in brf],
    }


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
    print(json.dumps(list_conversation_days(), ensure_ascii=False, indent=2))
