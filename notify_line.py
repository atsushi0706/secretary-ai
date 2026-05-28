"""LINE Messaging API へブリーフィングを Push 送信する。

設定:
  st.secrets / 環境変数のいずれかに次を入れる。
    LINE_CHANNEL_ACCESS_TOKEN  ... LINE Developers コンソールで発行する長期トークン
    LINE_USER_ID               ... 受信者(自分)の userId。Bot友達追加後にWebhook等で取得

提供:
  send_text(text)           生のテキストを送る
  send_briefing(date, mode) 朝/夜のブリーフィングを生成→送信(重複送信は防止)
  build_briefing(...)       本文生成のみ(送信しない)

呼び出し元:
  - app.py の クエリパラメータ ?notify=morning|evening から
  - 単独スクリプトとして `py notify_line.py morning` でも実行可
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Literal

import storage

try:
    import streamlit as _st
    _HAS_ST = True
except Exception:  # noqa: BLE001
    _HAS_ST = False

JST = dt.timezone(dt.timedelta(hours=9))
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _secret(name: str, default: str = "") -> str:
    if _HAS_ST:
        try:
            v = _st.secrets.get(name, "")
            if v:
                return str(v)
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(name, default)


def _truncate_for_line(text: str, limit: int = 4900) -> str:
    """LINEのテキスト1通は5000文字まで。余裕を持って切る。"""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…(以下省略)"


def send_text(text: str) -> dict:
    """LINEに1通テキストを送る。送信履歴は notifications テーブルに記録する。"""
    token = _secret("LINE_CHANNEL_ACCESS_TOKEN")
    user_id = _secret("LINE_USER_ID")
    if not token or not user_id:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID が未設定です。")

    body = json.dumps({
        "to": user_id,
        "messages": [{"type": "text", "text": _truncate_for_line(text)}],
    }).encode("utf-8")

    req = urllib.request.Request(
        LINE_PUSH_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            raw = resp.read().decode("utf-8", errors="replace")
            storage.log_notification("line", "raw", text, success=ok, error=raw if not ok else "")
            return {"ok": ok, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        msg = f"{e.code} {e.read().decode('utf-8', errors='replace')}"
        storage.log_notification("line", "raw", text, success=False, error=msg)
        raise
    except Exception as e:  # noqa: BLE001
        storage.log_notification("line", "raw", text, success=False, error=str(e))
        raise


def _format_events(events: list[dict], date_iso: str) -> str:
    lines = []
    for e in events:
        start = e.get("start") or ""
        if not start.startswith(date_iso):
            continue
        if e.get("all_day"):
            lines.append(f"・終日 {e['title']}")
        else:
            try:
                t = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(JST)
                lines.append(f"・{t:%H:%M} {e['title']}")
            except ValueError:
                lines.append(f"・{e['title']}")
    return "\n".join(lines) or "・予定なし"


def _format_tasks(tasks: list[dict], labels: dict, top: int = 6) -> str:
    """4象限の上から優先順に上位 top 件をまとめる。"""
    order = {
        ("high", "high"): 0,
        ("low", "high"): 1,
        ("high", "low"): 2,
        ("low", "low"): 3,
    }
    rows = []
    for t in tasks:
        lb = labels.get(t["id"], {})
        u, imp = lb.get("urgency", "low"), lb.get("importance", "low")
        rows.append((order.get((u, imp), 9), t["title"], lb.get("time", "today")))
    rows.sort()
    out = []
    time_emoji = {"quick": "⚡", "today": "📅", "days": "🗓"}
    for _, title, tlabel in rows[:top]:
        out.append(f"・{time_emoji.get(tlabel, '・')} {title}")
    if not out:
        return "・未完了タスクなし"
    return "\n".join(out)


def build_briefing(mode: Literal["morning", "evening"], target_date: dt.date) -> str:
    """LINE向けの簡潔ブリーフィング(プレーンテキスト)を組み立てる。"""
    import google_client as gc
    from classifier import classify_tasks

    events = gc.get_events(days_ahead=1)
    tasks = gc.get_tasks(include_completed=False)
    labels = classify_tasks(tasks)

    date_iso = target_date.isoformat()
    head = (
        f"☀️ おはようございます。\n今日({target_date:%m/%d})の予定とタスクです。"
        if mode == "morning"
        else f"🌙 おつかれさまでした。\n明日({target_date:%m/%d})の予定とタスクです。"
    )
    schedule_part = "■ 予定\n" + _format_events(events, date_iso)
    tasks_part = "■ タスク（優先順）\n" + _format_tasks(tasks, labels)
    foot = "\n秘書AIで詳しい時間割を見る → 朝モード/夜モードで会話してください。"
    return f"{head}\n\n{schedule_part}\n\n{tasks_part}{foot}"


def send_briefing(mode: Literal["morning", "evening"]) -> dict:
    """同日同種類の通知が成功済みなら何もしない（重複送信防止）。"""
    today = dt.datetime.now(JST).date()
    target_date = today if mode == "morning" else today + dt.timedelta(days=1)
    today_iso = today.isoformat()

    if storage.already_notified("line", mode, today_iso):
        return {"ok": True, "skipped": "already sent today"}

    text = build_briefing(mode, target_date)
    storage.save_briefing(today_iso, mode, text)
    res = send_text(text)
    # 上の send_text は type="raw" で保存するので、ここで type を mode 名に上書き
    storage.log_notification("line", mode, text, success=bool(res.get("ok")),
                             error="" if res.get("ok") else res.get("body", ""))
    return res


if __name__ == "__main__":
    storage.init_db()
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if mode_arg not in ("morning", "evening"):
        print("usage: py notify_line.py [morning|evening]")
        sys.exit(2)
    out = send_briefing(mode_arg)
    print(json.dumps(out, ensure_ascii=False))
