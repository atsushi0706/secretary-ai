"""ntfy.sh へ「秘書から連絡が届いた」風の通知を送る。

ntfy.sh はトピック名(URLパスの末尾)だけで購読・送信できる超軽量Pushサービス。
スマホに ntfy アプリを入れてトピックを Subscribe しておけば、
サーバーから HTTP POST で本文を送るだけで通知が届く。

設定:
  st.secrets / 環境変数のいずれかに次を入れる。
    NTFY_TOPIC      ... 推測されにくい固有のトピック名(例: kiyose-link-affection-9z3xq)
    NTFY_CLICK_URL  ... 通知タップ時に開くURL(秘書AIのURL)。任意。
    NTFY_SERVER     ... 自前サーバーを使う場合のホスト。既定は https://ntfy.sh

提供:
  send(title, body, click=None, priority='default', tags=None)
  send_briefing(mode)  朝/夜のブリーフィングを生成して送る(重複送信防止)
  build_briefing_short(mode, target_date)  通知向けの短い本文を作る
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
DEFAULT_SERVER = "https://ntfy.sh"


def _secret(name: str, default: str = "") -> str:
    if _HAS_ST:
        try:
            v = _st.secrets.get(name, "")
            if v:
                return str(v)
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(name, default)


def _encode_header(value: str) -> str:
    """ntfy のヘッダーは ASCII 制限。日本語は RFC2047(=?UTF-8?B?...?=) で送る。"""
    if value.isascii():
        return value
    import base64
    b64 = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{b64}?="


def send(title: str, body: str, *, click: str | None = None,
         priority: str = "default", tags: list[str] | None = None) -> dict:
    """ntfy にメッセージを1通送る。送信履歴は notifications テーブルに残す。"""
    topic = _secret("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("NTFY_TOPIC が未設定です。.streamlit/secrets.toml に追加してください。")
    server = _secret("NTFY_SERVER", DEFAULT_SERVER).rstrip("/")
    if not click:
        click = _secret("NTFY_CLICK_URL", "") or None

    headers: dict[str, str] = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": _encode_header(title),
        "Priority": priority,
    }
    if click:
        headers["Click"] = click
    if tags:
        headers["Tags"] = ",".join(tags)
    # ntfy トークン(自前サーバー or 予約トピック)があれば付ける
    token = _secret("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{server}/{topic}"
    req = urllib.request.Request(url, data=body.encode("utf-8"),
                                 method="POST", headers=headers)
    log_body = f"[{title}] {body}"
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            raw = resp.read().decode("utf-8", errors="replace")
            storage.log_notification("ntfy", "raw", log_body,
                                     success=ok, error=raw if not ok else "")
            return {"ok": ok, "status": resp.status, "body": raw}
    except urllib.error.HTTPError as e:
        msg = f"{e.code} {e.read().decode('utf-8', errors='replace')}"
        storage.log_notification("ntfy", "raw", log_body, success=False, error=msg)
        raise
    except Exception as e:  # noqa: BLE001
        storage.log_notification("ntfy", "raw", log_body, success=False, error=str(e))
        raise


def _format_top_events(events: list[dict], date_iso: str, top: int = 3) -> str:
    rows = []
    for e in events:
        start = e.get("start") or ""
        if not start.startswith(date_iso):
            continue
        if e.get("all_day"):
            rows.append(f"・終日 {e['title']}")
        else:
            try:
                t = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(JST)
                rows.append(f"・{t:%H:%M} {e['title']}")
            except ValueError:
                rows.append(f"・{e['title']}")
        if len(rows) >= top:
            break
    return "\n".join(rows) or "・予定なし"


def _format_top_tasks(tasks: list[dict], labels: dict, top: int = 4) -> str:
    order = {("high", "high"): 0, ("low", "high"): 1, ("high", "low"): 2, ("low", "low"): 3}
    rows = []
    for t in tasks:
        lb = labels.get(t["id"], {})
        rows.append((order.get((lb.get("urgency", "low"),
                                lb.get("importance", "low")), 9), t["title"]))
    rows.sort()
    out = [f"・{title}" for _, title in rows[:top]]
    return "\n".join(out) or "・未完了タスクなし"


def build_briefing_short(mode: Literal["morning", "evening"],
                         target_date: dt.date) -> tuple[str, str]:
    """通知向けの「タイトル」と「本文(要点だけ)」を組み立てる。"""
    import google_client as gc
    from classifier import classify_tasks

    events = gc.get_events(days_ahead=1)
    tasks = gc.get_tasks(include_completed=False)
    labels = classify_tasks(tasks)
    date_iso = target_date.isoformat()

    if mode == "morning":
        title = f"☀️ 清瀬リンクから（{target_date:%m/%d}の流れ）"
        head = "おはようございます。今日のブリーフィングが届いています。"
    else:
        title = f"🌙 清瀬リンクから（明日 {target_date:%m/%d}の準備）"
        head = "今日もおつかれさまでした。明日の段取りができています。"

    body_lines = [
        head,
        "",
        "■ 予定（上位）",
        _format_top_events(events, date_iso),
        "",
        "■ タスク（優先順）",
        _format_top_tasks(tasks, labels),
        "",
    ]
    # 本文末尾にURLをテキストで含める（iOSでもタップで開けるように）
    click_url = _secret("NTFY_CLICK_URL", "")
    if click_url:
        body_lines.append("👇 詳しい時間割を見る")
        body_lines.append(click_url)
    else:
        body_lines.append("詳しい時間割は秘書AIで見られます。")
    return title, "\n".join(body_lines)


def send_briefing(mode: Literal["morning", "evening"]) -> dict:
    """同日同種類の通知が成功済みなら何もしない（重複送信防止）。"""
    today = dt.datetime.now(JST).date()
    target_date = today if mode == "morning" else today + dt.timedelta(days=1)
    today_iso = today.isoformat()

    if storage.already_notified("ntfy", mode, today_iso):
        return {"ok": True, "skipped": "already sent today"}

    title, body = build_briefing_short(mode, target_date)
    storage.save_briefing(today_iso, mode, f"{title}\n\n{body}")
    res = send(title, body,
               tags=["sunny" if mode == "morning" else "crescent_moon"],
               priority="default")
    storage.log_notification("ntfy", mode, f"{title}\n\n{body}",
                             success=bool(res.get("ok")),
                             error="" if res.get("ok") else res.get("body", ""))
    return res


if __name__ == "__main__":
    storage.init_db()
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "morning"
    if mode_arg == "ping":
        out = send("✅ 清瀬リンクのテスト通知",
                   "これはテスト送信です。ntfyの設定が正しいか確認しています。",
                   tags=["white_check_mark"])
    elif mode_arg in ("morning", "evening"):
        out = send_briefing(mode_arg)
    else:
        print("usage: py notify_ntfy.py [morning|evening|ping]")
        sys.exit(2)
    print(json.dumps(out, ensure_ascii=False))
