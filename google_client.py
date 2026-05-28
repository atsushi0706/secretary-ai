"""Google カレンダー & Google ToDo(Tasks) との接続をまとめたモジュール。

ローカル運用: token_main.json / token_work.json をブラウザログインで生成し再利用。
Streamlit Cloud 運用: ローカルで作った token を st.secrets に貼り付けて読み出す。
   ファイルが無く secrets に値があれば、それを書き戻してから使う。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

try:  # Streamlit が無い実行環境(cron 単独実行など)でも import 可能にする
    import streamlit as _st
    _HAS_ST = True
except Exception:  # noqa: BLE001
    _HAS_ST = False

BASE_DIR = Path(__file__).parent
CREDENTIALS_FILE = BASE_DIR / "credentials.json"  # OAuthクライアント(2アカウント共通)
DATA_DIR = BASE_DIR / "data"

JST = dt.timezone(dt.timedelta(hours=9))

# 2アカウント構成:
#  main = affection … 予定(Calendar) / ToDo(Tasks) / Zoom文字起こし(Drive)
#  work = 仕事用    … メール(Gmail)だけ。仕事の連絡が届く先
WORK_EMAIL = os.environ.get("WORK_EMAIL", "mental.tuning.online@gmail.com")

ACCOUNTS: dict[str, dict] = {
    "main": {
        "token": DATA_DIR / "token_main.json",
        "scopes": [
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        "login_hint": "",  # ログイン時に普段のアカウントを選ぶ
    },
    "work": {
        "token": DATA_DIR / "token_work.json",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        "login_hint": WORK_EMAIL,  # 仕事用アカウントを選ぶよう誘導
    },
}


# ─────────────────────────────────────────────
# 認証(アカウント別にトークンを持つ)
# ─────────────────────────────────────────────
def _restore_token_from_secrets(account: str) -> bool:
    """Streamlit Cloud 等で token ファイルが無い場合に st.secrets から復元する。

    secrets.toml に次の形で書いておく:
      [google_tokens]
      main = '''{"token":"...","refresh_token":"..."}'''
      work = '''{"token":"...","refresh_token":"..."}'''
    """
    if not _HAS_ST:
        return False
    try:
        section = _st.secrets.get("google_tokens", {})
        payload = section.get(account)
    except Exception:  # noqa: BLE001
        return False
    if not payload:
        return False
    token_file: Path = ACCOUNTS[account]["token"]
    token_file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        token_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    else:
        token_file.write_text(str(payload), encoding="utf-8")
    return True


def is_authed(account: str = "main") -> bool:
    """そのアカウントが既にログイン済み(有効トークンあり)か。"""
    cfg = ACCOUNTS[account]
    token_file: Path = cfg["token"]
    if not token_file.exists():
        _restore_token_from_secrets(account)
    if not token_file.exists():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(token_file), cfg["scopes"])
    except Exception:  # noqa: BLE001
        return False
    return bool(creds and (creds.valid or (creds.expired and creds.refresh_token)))


def get_credentials(account: str = "main", allow_interactive: bool = True) -> Credentials | None:
    """指定アカウントのログイン情報を取得。

    allow_interactive=False のときは、未ログインでもブラウザを開かず None を返す
    (= アプリ起動時に仕事用ログイン待ちで固まらないようにするため)。
    account="main"(affection) と "work"(仕事用Gmail) で別トークンを保存する。
    """
    cfg = ACCOUNTS[account]
    token_file: Path = cfg["token"]
    scopes: list[str] = cfg["scopes"]

    if not token_file.exists():
        _restore_token_from_secrets(account)

    creds: Credentials | None = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif allow_interactive:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "credentials.json が見つかりません。README.md の手順で "
                    "Google Cloud から取得し、プロジェクト直下に置いてください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes)
            kwargs = {"port": 0}
            if cfg["login_hint"]:
                kwargs["login_hint"] = cfg["login_hint"]
            creds = flow.run_local_server(**kwargs)
        else:
            return None
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def connect_work_email() -> None:
    """仕事用Gmailの初回ログイン(明示的に呼んだときだけブラウザを開く)。"""
    get_credentials("work", allow_interactive=True)


def _calendar_service():
    return build("calendar", "v3", credentials=get_credentials("main"))


def _tasks_service():
    return build("tasks", "v1", credentials=get_credentials("main"))


def _drive_service():
    return build("drive", "v3", credentials=get_credentials("main"), cache_discovery=False)


def _gmail_service():
    # 自動取得では未ログインでもブラウザを開かない(固まり防止)。
    creds = get_credentials("work", allow_interactive=False)
    if creds is None:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ─────────────────────────────────────────────
# Zoom文字起こし(Drive上の .txt を読む)
# ─────────────────────────────────────────────
def get_recent_transcripts(days: int = 2, max_files: int = 5) -> list[dict]:
    """直近 days 日に更新された文字起こし(.txt / text/plain)を新しい順に返す。

    Zoom→Drive連携が `{相手名}/{日付}/{topic}.txt` で保存する前提。
    各要素: {name, modified, text}
    """
    svc = _drive_service()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    q = (
        "mimeType = 'text/plain' and trashed = false "
        f"and modifiedTime > '{since}'"
    )
    res = (
        svc.files()
        .list(
            q=q,
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=max_files,
        )
        .execute()
    )
    out: list[dict] = []
    for f in res.get("files", []):
        try:
            data = svc.files().get_media(fileId=f["id"]).execute()
            text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
        except Exception:  # noqa: BLE001
            text = ""
        out.append({"name": f["name"], "modified": f.get("modifiedTime", ""), "text": text})
    return out


# ─────────────────────────────────────────────
# 仕事用Gmail(仕事の連絡 → やるべきことの源)
# ─────────────────────────────────────────────
def get_work_emails(query: str = "newer_than:3d -category:promotions -category:social -category:forums",
                    max_results: int = 20) -> list[dict]:
    """仕事用アカウントの直近メール(宣伝/SNS/フォーラム除く)を新しい順に返す。

    本文の冒頭(snippet)まで取得し、依頼・連絡からタスクを拾えるようにする。
    各要素: {from, subject, date, snippet, unread}
    """
    svc = _gmail_service()
    if svc is None:
        return []  # 仕事用メール未連携 → 空で返す(アプリは止めない)
    listed = (
        svc.users().messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    out: list[dict] = []
    for m in listed.get("messages", []):
        msg = (
            svc.users().messages()
            .get(userId="me", id=m["id"], format="metadata",
                 metadataHeaders=["From", "Subject", "Date"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        out.append(
            {
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(件名なし)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
                "unread": "UNREAD" in msg.get("labelIds", []),
            }
        )
    return out


# ─────────────────────────────────────────────
# カレンダー(読み取り)
# ─────────────────────────────────────────────
# 予定の「埋まり具合」計算から除外するカレンダー(祝日など。表示はする)
NON_BUSY_CALENDAR_HINTS = ("holiday", "祝日")


def get_events(days_ahead: int = 1) -> list[dict]:
    """今日0時〜(今日+days_ahead)日後までの予定を、アクセスできる全カレンダー
    横断で時系列に返す。各予定に所属カレンダー名(calendar)も持たせる。"""
    svc = _calendar_service()
    now = dt.datetime.now(JST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=days_ahead + 1)

    calendars = svc.calendarList().list().execute().get("items", [])

    events: list[dict] = []
    for cal in calendars:
        cal_id = cal["id"]
        cal_name = cal.get("summary", "")
        is_holiday = any(h in (cal_id + cal_name).lower() for h in NON_BUSY_CALENDAR_HINTS)
        result = (
            svc.events()
            .list(
                calendarId=cal_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        for e in result.get("items", []):
            start_raw = e["start"].get("dateTime", e["start"].get("date"))
            end_raw = e.get("end", {}).get("dateTime", e.get("end", {}).get("date"))
            all_day = "date" in e["start"]
            events.append(
                {
                    "title": e.get("summary", "(無題)"),
                    "start": start_raw,
                    "end": end_raw,
                    "all_day": all_day,
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                    "calendar": cal_name,
                    "holiday": is_holiday,  # 空き時間計算では無視する
                }
            )
    events.sort(key=lambda x: x["start"] or "")
    return events


# ─────────────────────────────────────────────
# ToDo (Google Tasks / 読み書き)
# ─────────────────────────────────────────────
def get_task_lists() -> list[dict]:
    svc = _tasks_service()
    res = svc.tasklists().list(maxResults=100).execute()
    return [{"id": t["id"], "title": t["title"]} for t in res.get("items", [])]


def get_tasks(include_completed: bool = False) -> list[dict]:
    """全リストのタスクをまとめて返す。各タスクに所属リストIDも持たせる。"""
    svc = _tasks_service()
    all_tasks: list[dict] = []
    for tl in get_task_lists():
        res = (
            svc.tasks()
            .list(
                tasklist=tl["id"],
                showCompleted=include_completed,
                showHidden=include_completed,
                maxResults=100,
            )
            .execute()
        )
        for t in res.get("items", []):
            all_tasks.append(
                {
                    "id": t["id"],
                    "tasklist_id": tl["id"],
                    "tasklist_title": tl["title"],
                    "title": t.get("title", "(無題)"),
                    "notes": t.get("notes", ""),
                    "due": t.get("due"),  # RFC3339 or None
                    "status": t.get("status", "needsAction"),  # completed / needsAction
                    "updated": t.get("updated", ""),
                }
            )
    return all_tasks


def complete_task(tasklist_id: str, task_id: str) -> None:
    """タスクを完了にする(チェックを入れる)。"""
    svc = _tasks_service()
    svc.tasks().patch(
        tasklist=tasklist_id, task=task_id, body={"status": "completed"}
    ).execute()


def uncomplete_task(tasklist_id: str, task_id: str) -> None:
    """完了を取り消す。"""
    svc = _tasks_service()
    svc.tasks().patch(
        tasklist=tasklist_id, task=task_id, body={"status": "needsAction"}
    ).execute()


def compute_schedule(events: list[dict], target_date: dt.date,
                     work_start: int = 9, work_end: int = 17) -> dict:
    """指定日の『予定・空き時間』を計算して人が読める形でまとめる。

    work_end(既定17時)までを稼働時間とみなす。終日予定や稼働時間外の予定は
    空き時間計算からは除外するが、参考として一覧には載せる。
    """
    day_start = dt.datetime.combine(target_date, dt.time(work_start), JST)
    day_end = dt.datetime.combine(target_date, dt.time(work_end), JST)

    timed = []      # 稼働時間に重なる予定
    all_day = []    # 終日予定
    after_hours = []  # 稼働時間外(夜のZoom等)
    for e in events:
        if e.get("holiday"):
            continue  # 祝日カレンダーは予定・空き計算に含めない
        if e.get("all_day"):
            if (e.get("start") or "").startswith(target_date.isoformat()):
                all_day.append(e["title"])
            continue
        try:
            s = dt.datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(JST)
            en = dt.datetime.fromisoformat(e["end"].replace("Z", "+00:00")).astimezone(JST)
        except (ValueError, AttributeError):
            continue
        if s.date() != target_date:
            continue
        if en <= day_start or s >= day_end:
            after_hours.append((s, en, e["title"]))
        else:
            timed.append((max(s, day_start), min(en, day_end), e["title"]))

    timed.sort()
    # 空きスロットを算出
    free: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = day_start
    busy_minutes = 0
    for s, en, _ in timed:
        if s > cursor:
            free.append((cursor, s))
        busy_minutes += int((en - s).total_seconds() // 60)
        cursor = max(cursor, en)
    if cursor < day_end:
        free.append((cursor, day_end))

    free_minutes = sum(int((b - a).total_seconds() // 60) for a, b in free)

    def fmt(d: dt.datetime) -> str:
        return d.strftime("%H:%M")

    busy_text = "\n".join(f"  {fmt(s)}-{fmt(en)} {t}" for s, en, t in timed) or "  なし"
    free_text = "\n".join(f"  {fmt(a)}-{fmt(b)} ({int((b-a).total_seconds()//60)}分)" for a, b in free) or "  まとまった空きなし"
    after_text = "\n".join(f"  {fmt(s)}-{fmt(en)} {t}" for s, en, t in after_hours)

    return {
        "busy_minutes": busy_minutes,
        "free_minutes": free_minutes,
        "free_slots": free,
        "busy_text": busy_text,
        "free_text": free_text,
        "all_day": all_day,
        "after_hours_text": after_text,
        "work_start": work_start,
        "work_end": work_end,
    }


def delete_task(tasklist_id: str, task_id: str) -> None:
    """タスクを削除する。"""
    svc = _tasks_service()
    svc.tasks().delete(tasklist=tasklist_id, task=task_id).execute()


def update_task(tasklist_id: str, task_id: str, title: str | None = None,
                notes: str | None = None, due: str | None = None) -> dict:
    """タスクのタイトル/メモ/期限を更新する。"""
    body: dict = {}
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes
    if due is not None:
        body["due"] = f"{due}T00:00:00.000Z" if len(due) == 10 else due
    svc = _tasks_service()
    return svc.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()


def add_task(title: str, notes: str = "", due: str | None = None, tasklist_id: str = "@default") -> dict:
    """タスクを新規追加。due は 'YYYY-MM-DD' でも RFC3339 でも可。"""
    body: dict = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        if len(due) == 10:  # YYYY-MM-DD
            due = f"{due}T00:00:00.000Z"
        body["due"] = due
    svc = _tasks_service()
    return svc.tasks().insert(tasklist=tasklist_id, body=body).execute()
