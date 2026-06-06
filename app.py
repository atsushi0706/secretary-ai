"""秘書AI「清瀬リンク」── チャット会話 ＋ 4象限ボード(Streamlit / 作り込みUI)

使い方:
  py -m streamlit run app.py
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
from pathlib import Path

import markdown as _md
import streamlit as st

try:
    import extra_streamlit_components as stx
    _HAS_STX = True
except Exception:  # noqa: BLE001
    _HAS_STX = False

import google_client as gc
import storage
from classifier import (
    QUADRANT_LABEL,
    SECRETARY_NAME,
    TIME_LABEL,
    build_context_block,
    classify_tasks,
    clear_manual_label,
    extract_tasks_from_conversation,
    extract_tasks_from_image,
    secretary_chat,
    set_manual_label,
)

storage.init_db()


def _notify_endpoint():
    """クエリパラメータ ?notify=morning|evening でアクセスされたとき、
    LINE Push を実行して即終了する。cron-job.org など外部 cron から叩く。

    安全鍵: secrets に NOTIFY_KEY を設定し、?key=... が一致した時だけ実行する。
    """
    qp = dict(st.query_params)
    mode_q = qp.get("notify")
    if not mode_q:
        return
    try:
        expected_key = st.secrets.get("NOTIFY_KEY", "")
    except Exception:  # noqa: BLE001
        expected_key = os.environ.get("NOTIFY_KEY", "")
    if expected_key and qp.get("key") != expected_key:
        st.write("403"); st.stop()
    if mode_q not in ("morning", "evening", "ping"):
        st.write("400: notify must be morning|evening|ping"); st.stop()
    try:
        from notify_ntfy import send, send_briefing
        if mode_q == "ping":
            res = send("✅ 清瀬リンクのテスト通知",
                       "これはテスト送信です。ntfy の設定が正しいか確認しています。",
                       tags=["white_check_mark"])
        else:
            res = send_briefing(mode_q)
        st.write({"notify": mode_q, "result": res})
    except Exception as e:  # noqa: BLE001
        st.write({"notify": mode_q, "error": str(e)})
    st.stop()


_notify_endpoint()

st.set_page_config(page_title=f"秘書AI {SECRETARY_NAME}", page_icon="🗒️", layout="wide")

def _try_secret(key: str, default: str = "") -> str:
    """secrets.toml が無い環境(Render等)でも安全にsecretを取る。"""
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:  # noqa: BLE001
        pass
    return default


# secrets.toml に値があれば環境変数にコピー（Render環境では既に環境変数経由）
for _k in ("GEMINI_API_KEY", "GEMINI_MODEL", "WORK_EMAIL"):
    _v = _try_secret(_k)
    if _v and not os.environ.get(_k):
        os.environ[_k] = _v


_AUTH_COOKIE = "secretary_authed"


def _auth_token(pw: str) -> str:
    return hashlib.sha256(f"{pw}::secretary-ai-cookie-v1".encode()).hexdigest()[:32]


def _cookie_manager():
    """セッションに1つだけ CookieManager を持つ。"""
    if not _HAS_STX:
        return None
    if "_cm" not in st.session_state:
        st.session_state["_cm"] = stx.CookieManager(key="secretary_cm")
    return st.session_state["_cm"]


def _password_gate():
    """合言葉ゲートは撤廃。URLが推測不可能な前提でフリーアクセス。
    APP_PASSWORD は secrets に残ってても無視する。"""
    return


_password_gate()

JST = dt.timezone(dt.timedelta(hours=9))
WORK_START_HOUR, WORK_END_HOUR = 9, 17
BASE_DIR = Path(__file__).parent
AVATAR = BASE_DIR / "assets" / "kiyose.png"

URGENCY_OPTS = {"高い": "high", "低い": "low"}
IMPORTANCE_OPTS = {"高い": "high", "低い": "low"}
TIME_OPTS = {"すぐ終わる(5〜20分)": "quick", "今日中(30分〜3h)": "today", "数日かかる": "days"}

# 4象限の色（カードのアクセント）
QUAD_COLOR = {
    ("high", "high"): "#e2574c",
    ("low", "high"): "#e0a82e",
    ("high", "low"): "#3a78c2",
    ("low", "low"): "#9aa0a6",
}


@st.cache_data
def _avatar_uri() -> str:
    if AVATAR.exists():
        b64 = base64.b64encode(AVATAR.read_bytes()).decode()
        return f"data:image/png;base64,{b64}"
    return ""


AVATAR_URI = _avatar_uri()


# ─────────────────────────────────────────────
# スタイル(作り込み)
# ─────────────────────────────────────────────
def inject_css():
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans JP', sans-serif; color:#2c2c2e; }
#MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
.stApp { background: linear-gradient(160deg, #fafbff 0%, #f3f0fa 60%, #ecf2fb 100%); }
.block-container { padding-top: 1.4rem; padding-bottom: 6rem; max-width: 1480px; }

/* ヘッダーカード */
.hero { display:flex; align-items:center; gap:18px; background:#ffffff;
  border-radius:24px; padding:20px 28px; box-shadow:0 10px 32px rgba(80,80,130,.10);
  margin-bottom:20px; border:1px solid rgba(200,200,230,.45);
  background:linear-gradient(135deg, #ffffff 0%, #faf8ff 100%); }
.hero img { width:68px; height:68px; border-radius:50%; object-fit:cover;
  border:3px solid #eee5f5; box-shadow:0 4px 12px rgba(122,109,214,.18); }
.hero .nm { font-weight:700; font-size:1.22rem; color:#2c2c2e;
  letter-spacing:.2px; }
.hero .sub { font-size:.82rem; color:#8a8a90; margin-top:3px; }
.hero .dot { color:#3fb27f; font-size:.78rem; }
.hero .badge { display:inline-block; background:#ebf6f0; color:#2c8a5b;
  font-size:.7rem; font-weight:600; padding:2px 8px; border-radius:10px;
  margin-left:6px; }

/* チャット吹き出し（モック寄せ：丸み・余白・パステル影） */
.chatwrap { display:flex; flex-direction:column; gap:14px; padding:6px 2px 10px; }
.row { display:flex; align-items:flex-end; gap:10px; }
.row.bot { justify-content:flex-start; }
.row.me  { justify-content:flex-end; }
.ava { width:40px; height:40px; border-radius:50%; object-fit:cover;
  border:2px solid #eee5f5; flex:0 0 auto; }
.bub { max-width:82%; padding:14px 18px; border-radius:20px; line-height:1.8;
  font-size:.96rem; box-shadow:0 3px 12px rgba(80,80,130,.06); }
.bub.bot { background:#ffffff; color:#2c2c2e; border-top-left-radius:6px;
  border:1px solid rgba(200,200,230,.4); }
.bub.me  { background:linear-gradient(135deg,#7a6dd6,#6358c5); color:#fff;
  border-top-right-radius:6px; }
.bub p { margin:.2em 0; } .bub ul { margin:.3em 0; padding-left:1.1em; }
.bub strong { font-weight:700; }

/* チャット内の時刻スロット（19:30 - 20:00 ◯◯ を色付きバッジに変換) */
.bub .slot { display:flex; align-items:center; gap:10px; padding:6px 10px;
  background:#f7f5ff; border-left:3px solid #7a6dd6; border-radius:8px;
  margin:6px 0; font-size:.88rem; }
.bub .slot .time { background:#ede8f9; color:#5a4ab8; padding:3px 9px;
  border-radius:6px; font-weight:700; font-size:.78rem; min-width:96px;
  text-align:center; letter-spacing:.3px; font-variant-numeric:tabular-nums; }
.bub .slot .lbl { color:#2c2c2e; font-weight:500; }

/* タスクマトリックス */
.boardttl { font-weight:700; color:#3a3a3e; margin:2px 0 12px; font-size:1.06rem;
  display:flex; align-items:center; gap:8px; }
.qhead { font-weight:700; font-size:.88rem; padding:8px 12px; border-radius:10px;
  background:#fafafd; margin-bottom:6px; display:flex; align-items:center; gap:6px;
  border-bottom:1px solid rgba(200,200,230,.3); }
.qcount { background:#f0eefa; color:#5a4ab8; font-size:.7rem; padding:1px 8px;
  border-radius:10px; font-weight:600; margin-left:auto; }
div[data-testid="stVerticalBlockBorderWrapper"] { background:#ffffffe6;
  border-radius:16px; border:1px solid rgba(200,200,230,.4);
  box-shadow:0 4px 18px rgba(80,80,130,.05); }
.stButton button { border-radius:12px; }

/* タスク行（1行コンパクト） */
.taskrow { font-size:.85rem; line-height:1.45; color:#2c2c2e; padding:1px 0; }
.taskrow .title { font-weight:500; }
.taskrow .ttag, .taskrow .dtag, .taskrow .mtag {
  display:inline-block; padding:1px 6px; border-radius:6px;
  font-size:.68rem; margin-left:4px; vertical-align:middle; font-weight:600;
}
.taskrow .ttag { background:#eef0fa; color:#5a4ab8; }
.taskrow .dtag { background:#fde4e4; color:#b84f4f; }
.taskrow .mtag { background:#f5f5f5; color:#888; }
/* popoverトリガをコンパクトに */
[data-testid="stPopover"] > div > button {
  padding:2px 8px !important; font-size:.85rem !important;
  min-height: 28px !important; line-height:1 !important;
}
.stButton button[kind="primary"] {
  background:linear-gradient(135deg,#7a6dd6,#6358c5); border:none; }
[data-testid="stChatInput"] textarea { font-family:'Noto Sans JP',sans-serif; }
[data-testid="stChatInput"] { border-radius:18px; }

/* サイドバー */
[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid rgba(200,200,230,.4); }
[data-testid="stSidebar"] .stMarkdown h2 { font-size:1.05rem; color:#3a3a3e; }

/* 今日のアドバイス枠 */
.advice { background:#f6f3ff; border-radius:14px; padding:14px 18px;
  border:1px solid #e5dffb; margin-top:14px; }
.advice .ttl { font-weight:700; color:#5a4ab8; font-size:.92rem; margin-bottom:4px; }
.advice .body { color:#3a3a3e; font-size:.88rem; line-height:1.7; }
</style>
        """,
        unsafe_allow_html=True,
    )


inject_css()


# ─────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def load_data():
    return gc.get_events(days_ahead=1), gc.get_tasks(include_completed=False)


@st.cache_data(ttl=300, show_spinner=False)
def load_transcripts(days: int):
    try:
        return gc.get_recent_transcripts(days=days)
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(ttl=600, show_spinner=False)
def load_transcripts_metadata(days: int):
    """過去 days 日のZoom文字起こし一覧（本文なし・日付・相手名つき）。"""
    try:
        return gc.list_transcripts_metadata(days=days)
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(ttl=300, show_spinner=False)
def load_work_emails():
    try:
        return gc.get_work_emails()
    except Exception:  # noqa: BLE001
        return []


def refresh():
    load_data.clear()


def fmt_time(raw: str, all_day: bool) -> str:
    if all_day:
        return "終日"
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(JST).strftime("%H:%M")
    except ValueError:
        return raw


def _friendly_error(e: Exception) -> str:
    if any(c in str(e) for c in ("503", "UNAVAILABLE", "高い需要", "overloaded", "429")):
        return "いまGemini(無料枠)が混み合っているようです。少し待って🔄か再読み込みで試してください。"
    return f"うまく応答できませんでした。少し待って再試行してください。（{str(e)[:120]}）"


# ─────────────────────────────────────────────
# サイドバー
# ─────────────────────────────────────────────
if AVATAR.exists():
    st.sidebar.image(str(AVATAR), use_container_width=True)
st.sidebar.title(f"秘書 {SECRETARY_NAME}")
mode = st.sidebar.radio("いつの相談？", ["🌅 朝（今日の組み立て）", "🌙 夜（明日の準備）"], index=0)
focus = st.sidebar.toggle("🗨️ 集中モード（会話だけ）", value=False)
if st.sidebar.button("🔄 予定とタスクを最新に"):
    refresh()
    st.session_state.pop("chat_key", None)
    st.rerun()

with st.sidebar.expander("📎 スクショからタスク抽出"):
    st.caption("メールやメッセージのスクショをアップすると、Geminiが文字を読んでタスクを作ります。")
    _img = st.file_uploader(
        "画像をアップロード", type=["png", "jpg", "jpeg", "webp"],
        label_visibility="collapsed", key="img_upload",
    )
    _img_hint = st.text_input(
        "補足ヒント（任意）", key="img_hint",
        placeholder="例：返信は明日まで",
    )
    if _img is not None:
        st.image(_img, caption=_img.name, width="stretch")
        if st.button("📸 画像からタスクを読み取る", type="primary", use_container_width=True):
            try:
                with st.spinner("画像を解析中…"):
                    cands = extract_tasks_from_image(
                        _img.getvalue(),
                        _img.type or "image/png",
                        target_label=target_label,
                        target_date_iso=target_day.isoformat(),
                        extra_hint=_img_hint or "",
                    )
                if cands:
                    st.session_state["task_candidates"] = cands
                    st.success(f"{len(cands)}件抽出。下のタスク候補で確認・編集→Googleタスクに追加してください。")
                else:
                    st.info("画像からタスクは見つかりませんでした。")
            except Exception as e:  # noqa: BLE001
                st.error(f"画像解析エラー: {e}")

with st.sidebar.expander("📲 スマホ通知（ntfy）"):
    if st.button("いまのブリーフィングを送る"):
        try:
            from notify_ntfy import send_briefing as _sb
            with st.spinner("送信中…"):
                res = _sb("morning" if mode.startswith("🌅") else "evening")
            if res.get("skipped"):
                st.info(f"本日は送信済みです: {res['skipped']}")
            elif res.get("ok"):
                st.success("送りました。スマホを見てください。")
            else:
                st.error(f"失敗: {res}")
        except Exception as e:  # noqa: BLE001
            st.error(f"通知エラー: {e}")
    if st.button("テスト通知（ping）"):
        try:
            from notify_ntfy import send as _send
            res = _send("✅ 清瀬リンクのテスト通知",
                        "これはテスト送信です。ntfy の設定が正しいか確認しています。",
                        tags=["white_check_mark"])
            if res.get("ok"):
                st.success("テスト通知を送りました。スマホで受け取れたら成功です。")
            else:
                st.error(f"失敗: {res}")
        except Exception as e:  # noqa: BLE001
            st.error(f"通知エラー: {e}")

st.sidebar.caption("稼働は9〜17時。カレンダーの時間軸に沿って今日の流れを提案します。")

# クイックメモ（SQLiteに保存）
with st.sidebar:
    st.markdown("### 📝 クイックメモ")
    _qm_current = storage.load_quickmemo()
    _qm_new = st.text_area(
        "気づいたことをメモ", value=_qm_current,
        key="quickmemo_input", height=120,
        label_visibility="collapsed",
        placeholder="気づいたことを\nメモできます…",
    )
    if _qm_new != _qm_current:
        storage.save_quickmemo(_qm_new)
        st.caption("✓ 自動保存")


# ─────────────────────────────────────────────
# データ・分類・状況
# ─────────────────────────────────────────────
try:
    events, tasks = load_data()
except FileNotFoundError as e:
    st.error(str(e)); st.info("README.md の初回セットアップを参照してください。"); st.stop()
except Exception as e:  # noqa: BLE001
    st.error(f"Googleへの接続でエラー: {e}"); st.stop()

with st.spinner("状況を確認中..."):
    labels = classify_tasks(tasks)

today = dt.date.today()
is_morning = mode.startswith("🌅")
mode_key = "morning" if is_morning else "evening"
target_day = today if is_morning else today + dt.timedelta(days=1)
target_label = "今日" if is_morning else "明日"

recent_transcripts = load_transcripts(days=7)
transcript_metadata = load_transcripts_metadata(days=30)
recent_emails = load_work_emails() if gc.is_authed("work") else []
auto_transcript = "\n\n".join(
    f"=== {t.get('person','')} / {t.get('topic_title', t['name'])} "
    f"({t['modified'][:10]}) ===\n{t['text']}"
    for t in recent_transcripts
)
now_jst = dt.datetime.now(JST)
schedule = gc.compute_schedule(events, target_day, work_start=WORK_START_HOUR,
                               work_end=WORK_END_HOUR, now=now_jst)
context_block = build_context_block(events, tasks, labels, schedule,
                                    transcript=auto_transcript, emails=recent_emails,
                                    target_label=target_label, now=now_jst,
                                    transcript_metadata=transcript_metadata)


# ─────────────────────────────────────────────
# ヘッダーカード
# ─────────────────────────────────────────────
wd = ["月", "火", "水", "木", "金", "土", "日"][today.weekday()]
st.markdown(
    f"""
<div class="hero">
  <img src="{AVATAR_URI}"/>
  <div>
    <div class="nm">{SECRETARY_NAME} <span class="dot">●</span><span class="sub"> オンライン</span></div>
    <div class="sub">{today:%Y年%m月%d日}（{wd}）・{target_label}の時間割を一緒に組みましょう</div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────
# 会話の準備（清瀬リンクが先に話す）
# ─────────────────────────────────────────────
chat_key = f"{today.isoformat()}_{mode_key}"
if st.session_state.get("chat_key") != chat_key:
    st.session_state["chat_key"] = chat_key
    # SQLiteから過去の会話を復元（同じ日・同じモードの履歴を引き継ぐ）
    st.session_state["messages"] = storage.load_messages(today.isoformat(), mode_key)
    st.session_state["task_candidates"] = []

if is_morning:
    opening = (
        f"挨拶（今が朝なら『おはよう』、昼以降なら『おつかれさまです』など現在時刻に合ったもの）から始めて、"
        f"今日の【現在時刻 {now_jst.strftime('%H:%M')} から 17時まで】の時間割のたたき台を時刻つきで提案してください。"
        "【絶対】現在時刻より前の枠（既に過ぎた時間）には何も入れないこと。"
        "【絶対】固定の予定(会議・セッション)は省略せず、必ずその時刻のまま時間割に含めること。"
        "予定の合間の空き時間に、優先度の高いタスクから当てはめてください。"
        "17時以降に夜の予定（Zoom等）がある場合は、時間割の末尾に『夜の予定として参考』として時刻つきで併記する。"
        "最後に「この流れでいけそうですか？調整したいところはありますか？」と短く尋ねてください。"
    )
else:
    opening = (
        "今日もおつかれさまでした、とねぎらってから、"
        "【明日の予定(9〜17時)】から逆算して、明日の時間割のたたき台を時刻つきで提示してください。"
        "明日の固定予定(会議・セッション等)は省略禁止。その合間の空き時間に、"
        "未完了タスクの中で『明日着手すべきもの』を優先度順に当てはめてください。"
        "重要だが緊急ではないものも、明日できる最小ステップに分解して入れてください。"
        "明日の17時以降に夜の予定（Zoom等）がある場合は、時間割の末尾に『夜の予定として参考』として時刻つきで併記する。"
        "最後に『明日はこの順で進めて大丈夫そうですか？追加でやりたいことや、外したいものはありますか？』と短く尋ねてください。"
    )

if not st.session_state["messages"]:
    try:
        with st.spinner(f"{SECRETARY_NAME}が{target_label}の流れを考えています…"):
            first = secretary_chat([], context_block, user_input=opening)
        st.session_state["messages"].append({"role": "assistant", "content": first})
        storage.save_message(today.isoformat(), mode_key, "assistant", first)
    except Exception as e:  # noqa: BLE001
        st.warning(_friendly_error(e))
        if st.button("↻ もう一度試す"):
            st.rerun()
        st.stop()


import re as _re

_SLOT_PATTERN = _re.compile(
    r'^[\s・\-\*●○◇◆]*(\d{1,2}:\d{2})\s*[-〜~–—]\s*(\d{1,2}:\d{2})\s*[:：]?\s*(.+?)\s*$',
    _re.MULTILINE,
)


def _slotify(text: str) -> str:
    """秘書AIの応答内に出てくる『19:30-20:00 ○○』を色付きカードに変換する。"""
    def repl(m):
        return (
            f'<div class="slot">'
            f'<span class="time">{m.group(1)}–{m.group(2)}</span>'
            f'<span class="lbl">{m.group(3)}</span>'
            f'</div>'
        )
    return _SLOT_PATTERN.sub(repl, text)


def render_chat():
    rows = []
    for m in st.session_state["messages"]:
        # assistant のメッセージは時刻スロットを色付き化してから markdown 変換
        raw = m["content"]
        if m["role"] == "assistant":
            raw = _slotify(raw)
        body = _md.markdown(raw, extensions=["nl2br", "sane_lists"])
        if m["role"] == "assistant":
            rows.append(
                f'<div class="row bot"><img class="ava" src="{AVATAR_URI}"/>'
                f'<div class="bub bot">{body}</div></div>'
            )
        else:
            rows.append(f'<div class="row me"><div class="bub me">{body}</div></div>')
    st.markdown(f'<div class="chatwrap">{"".join(rows)}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────
# タスク（4象限ボード）描画パーツ
# ─────────────────────────────────────────────
def render_task_extractor():
    """会話末尾の『話した内容をタスク化』フロー。

    朝モード: 今日着手すべきタスクを抽出して Google タスクに追加
    夜モード: 明日やるべきタスクを抽出して期限=明日で Google タスクに追加
    """
    btn_label = (
        "✨ ここまでの会話を今日のタスクに反映"
        if is_morning
        else "📅 ここまでの会話から明日のタスクを作る"
    )
    cols = st.columns([4, 1])
    if cols[0].button(btn_label, use_container_width=True, type="secondary"):
        try:
            with st.spinner(f"{SECRETARY_NAME}が会話からタスクを抜き出しています…"):
                cands = extract_tasks_from_conversation(
                    st.session_state["messages"],
                    context_block,
                    target_label=target_label,
                    target_date_iso=target_day.isoformat(),
                    mode=mode_key,
                )
            st.session_state["task_candidates"] = cands
            if not cands:
                st.info("会話の中から新規追加すべきタスクは見つかりませんでした。")
        except Exception as e:  # noqa: BLE001
            st.warning(_friendly_error(e))
    if cols[1].button("クリア", use_container_width=True):
        st.session_state["task_candidates"] = []
        st.rerun()

    cands = st.session_state.get("task_candidates") or []
    if not cands:
        return

    st.markdown("#### 抽出されたタスク候補（編集してから追加）")
    edited_rows = []
    for i, c in enumerate(cands):
        with st.container(border=True):
            r1 = st.columns([1, 6, 3])
            include = r1[0].checkbox("追加", value=True, key=f"cand_inc_{i}")
            title = r1[1].text_input("タイトル", value=c["title"], key=f"cand_title_{i}",
                                     label_visibility="collapsed")
            due_default = c.get("due") or (target_day.isoformat() if not is_morning else "")
            has_due = bool(due_default)
            due_on = r1[2].checkbox("期限あり",
                                    value=has_due, key=f"cand_due_chk_{i}")
            r2 = st.columns(4)
            u_idx = 0 if c["urgency"] == "high" else 1
            i_idx = 0 if c["importance"] == "high" else 1
            t_idx = next((n for n, v in enumerate(TIME_OPTS.values()) if v == c["time"]), 1)
            u = r2[0].selectbox("緊急度", list(URGENCY_OPTS), index=u_idx, key=f"cand_u_{i}")
            imp = r2[1].selectbox("重要度", list(IMPORTANCE_OPTS), index=i_idx, key=f"cand_i_{i}")
            tm = r2[2].selectbox("所要時間", list(TIME_OPTS), index=t_idx, key=f"cand_t_{i}")
            due_val = r2[3].date_input(
                "期限", value=target_day if due_on else today,
                key=f"cand_due_{i}", disabled=not due_on, label_visibility="visible")
            if c.get("reason"):
                st.caption(f"理由: {c['reason']}")
            edited_rows.append({
                "include": include,
                "title": title.strip(),
                "notes": c.get("notes", ""),
                "urgency": URGENCY_OPTS[u],
                "importance": IMPORTANCE_OPTS[imp],
                "time": TIME_OPTS[tm],
                "due": due_val.isoformat() if due_on else None,
                "reason": c.get("reason", ""),
            })

    if st.button("✅ チェックしたタスクをGoogleタスクに追加",
                 type="primary", use_container_width=True):
        added = 0
        for r in edited_rows:
            if not r["include"] or not r["title"]:
                continue
            ext_id = storage.save_extracted_task(
                date=target_day.isoformat(), mode=mode_key, source="conversation",
                title=r["title"], notes=r["notes"], due=r["due"],
                urgency=r["urgency"], importance=r["importance"],
                time_label=r["time"],
            )
            try:
                created = gc.add_task(r["title"], notes=r["notes"], due=r["due"])
                set_manual_label(created["id"], r["urgency"], r["importance"], r["time"])
                storage.mark_task_pushed(ext_id, created["id"])
                added += 1
            except Exception as e:  # noqa: BLE001
                st.warning(f"追加失敗: {r['title']} — {e}")
        if added:
            st.success(f"{added}件のタスクをGoogleタスクに追加しました。")
            st.session_state["task_candidates"] = []
            refresh()
            st.rerun()


def render_task(t: dict):
    lb = labels.get(t["id"], {})
    time_badge = TIME_LABEL.get(lb.get("time", "today"), "")
    manual_mark = "✋" if lb.get("manual") else ""
    due = ""
    if t.get("due"):
        try:
            d = dt.datetime.fromisoformat(t["due"].replace("Z", "+00:00")).date()
            due = f"〆{d:%m/%d}"
        except ValueError:
            pass
    c_chk, c_lbl, c_btn = st.columns([1, 12, 2])
    with c_chk:
        checked = st.checkbox(t["title"], key=f"chk_{t['id']}", label_visibility="collapsed")
    with c_lbl:
        badges = f'<span class="ttag">{time_badge}</span>'
        if due:
            badges += f'<span class="dtag">{due}</span>'
        if manual_mark:
            badges += f'<span class="mtag">{manual_mark}</span>'
        st.markdown(
            f'<div class="taskrow"><span class="title">{t["title"]}</span> {badges}</div>',
            unsafe_allow_html=True,
        )
    with c_btn:
        with st.popover("⋮", help="詳細・編集・削除"):
            u = st.selectbox("緊急度", list(URGENCY_OPTS),
                             index=0 if lb.get("urgency") == "high" else 1, key=f"u_{t['id']}")
            imp = st.selectbox("重要度", list(IMPORTANCE_OPTS),
                               index=0 if lb.get("importance") == "high" else 1, key=f"i_{t['id']}")
            tm_idx = next((n for n, v in enumerate(TIME_OPTS.values()) if v == lb.get("time")), 1)
            tm = st.selectbox("所要時間", list(TIME_OPTS), index=tm_idx, key=f"t_{t['id']}")
            if st.button("この分類で保存", key=f"save_{t['id']}", use_container_width=True):
                set_manual_label(t["id"], URGENCY_OPTS[u], IMPORTANCE_OPTS[imp], TIME_OPTS[tm])
                refresh(); st.rerun()
            if st.button("AI判定に戻す", key=f"auto_{t['id']}", use_container_width=True):
                clear_manual_label(t["id"]); refresh(); st.rerun()
            if st.button("🗑 このタスクを削除", key=f"del_{t['id']}", use_container_width=True):
                gc.delete_task(t["tasklist_id"], t["id"]); clear_manual_label(t["id"])
                st.toast(f"🗑 削除: {t['title']}"); refresh(); st.rerun()
    if checked:
        gc.complete_task(t["tasklist_id"], t["id"])
        st.toast(f"✅ 完了: {t['title']}"); refresh(); st.rerun()


QUAD_SHORT = {
    ("high", "high"): "🔴 緊急 × 重要",
    ("low", "high"): "🟡 重要だが緊急でない",
    ("high", "low"): "🔵 緊急だが重要度が低い",
    ("low", "low"): "⚪ 緊急度も重要度も低い",
}


def render_quadrant(key: tuple, items: list):
    """4象限カード1枚分を描画する。タスクが多くてもカードは固定高で縦スクロール。"""
    color = QUAD_COLOR[key]
    with st.container(border=True, height=360):
        st.markdown(
            f'<div class="qhead" style="border-left:5px solid {color}">'
            f'{QUAD_SHORT[key]}  <span class="qcount">{len(items)}</span></div>',
            unsafe_allow_html=True,
        )
        if not items:
            st.caption("なし")
        for t in items:
            render_task(t)


def render_board():
    st.markdown(
        '<div class="boardttl">🗂️ タスクマトリックス（緊急度 × 重要度）</div>',
        unsafe_allow_html=True,
    )
    with st.expander("➕ タスクを追加"):
        with st.form("add_task_form", clear_on_submit=True):
            new_title = st.text_input("やること", placeholder="例：企画書を仕上げる")
            has_due = st.checkbox("期限あり")
            due_date = st.date_input("期限", value=today, label_visibility="collapsed")
            cls_mode = st.radio("優先度", ["AIにおまかせ", "自分で指定"], horizontal=True)
            m1, m2, m3 = st.columns(3)
            u = m1.selectbox("緊急度", list(URGENCY_OPTS), key="add_u")
            imp = m2.selectbox("重要度", list(IMPORTANCE_OPTS), key="add_i")
            tm = m3.selectbox("所要時間", list(TIME_OPTS), key="add_t")
            if st.form_submit_button("Googleタスクに追加", type="primary") and new_title.strip():
                created = gc.add_task(new_title.strip(),
                                      due=due_date.isoformat() if has_due else None)
                if cls_mode == "自分で指定":
                    set_manual_label(created["id"], URGENCY_OPTS[u], IMPORTANCE_OPTS[imp], TIME_OPTS[tm])
                st.toast(f"➕ 追加: {new_title}"); refresh(); st.rerun()

    quadrants = {k: [] for k in QUADRANT_LABEL}
    for t in tasks:
        lb = labels.get(t["id"], {})
        quadrants[(lb.get("urgency", "low"), lb.get("importance", "low"))].append(t)

    # 2×2 グリッド配置（モックUIに合わせて）
    # 上段: 左=重要だが緊急でない / 右=緊急かつ重要
    # 下段: 左=重要度が低く緊急でない / 右=緊急だが重要度が低い
    st.caption("↑ 重要度 高　／　→ 緊急度 高")
    row1_left, row1_right = st.columns(2, gap="small")
    with row1_left:
        render_quadrant(("low", "high"), quadrants[("low", "high")])
    with row1_right:
        render_quadrant(("high", "high"), quadrants[("high", "high")])
    row2_left, row2_right = st.columns(2, gap="small")
    with row2_left:
        render_quadrant(("low", "low"), quadrants[("low", "low")])
    with row2_right:
        render_quadrant(("high", "low"), quadrants[("high", "low")])


# ─────────────────────────────────────────────
# レイアウト：左=会話 / 右=4象限ボード（集中モードなら会話のみ）
# ─────────────────────────────────────────────
def render_today_advice():
    """ルールベースの『今日のアドバイス』。Kiyo Blackらしく短くドライに。"""
    parts = []
    busy_min = schedule.get("busy_minutes", 0)
    free_min = schedule.get("free_minutes", 0)
    high_high = sum(
        1 for t in tasks
        if labels.get(t["id"], {}).get("urgency") == "high"
        and labels.get(t["id"], {}).get("importance") == "high"
    )
    if busy_min > 240:
        parts.append("今日は予定詰めめ。制作系は最小着手で。")
    if high_high >= 3:
        parts.append(f"緊急×重要が{high_high}件。まずそこから片付ける。")
    if free_min and free_min < 60:
        parts.append("空き時間が少ない。スキマ時間で軽いやつをまとめて処理。")
    if not parts:
        if high_high == 0:
            parts.append("今日は緊急タスクなし。重要だが急がないやつ進めるチャンスだよ。")
        else:
            parts.append("時間は十分。優先度通りに淡々と進めればいい。")
    advice_text = " / ".join(parts)
    st.markdown(
        f'<div class="advice">'
        f'<div class="ttl">✨ 今日のアドバイス</div>'
        f'<div class="body">{advice_text}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


if focus:
    render_chat()
    render_task_extractor()
else:
    # チャット側を狭め、マトリクス側を広く(比率 2:3)
    col_chat, col_board = st.columns([2, 3], gap="large")
    with col_chat:
        render_chat()
        render_task_extractor()
    with col_board:
        render_board()
        render_today_advice()


# 入力（音声入力もここから）
if prompt := st.chat_input("気分・優先したいこと・調整したい時間帯など…（音声入力でもOK）"):
    st.session_state["messages"].append({"role": "user", "content": prompt})
    storage.save_message(today.isoformat(), mode_key, "user", prompt)
    try:
        with st.spinner("考えています…"):
            reply = secretary_chat(st.session_state["messages"], context_block)
        st.session_state["messages"].append({"role": "assistant", "content": reply})
        storage.save_message(today.isoformat(), mode_key, "assistant", reply)
    except Exception as e:  # noqa: BLE001
        err_msg = "（" + _friendly_error(e) + "）"
        st.session_state["messages"].append({"role": "assistant", "content": err_msg})
        storage.save_message(today.isoformat(), mode_key, "assistant", err_msg)
    st.rerun()


# 情報源 / 仕事メール連携（サイドバー）
with st.sidebar.expander("📥 秘書が見ている情報源"):
    for e in [e for e in events if (e["start"] or "").startswith(today.isoformat())]:
        st.markdown(f"- {fmt_time(e['start'], e['all_day'])} {e['title']}　_{e.get('calendar','')}_")
    if transcript_metadata:
        st.caption(f"Zoom履歴 (直近30日 / 本文は直近7日のみ): {len(transcript_metadata)}件")
        for t in transcript_metadata[:15]:
            person = t.get("person") or ""
            topic = t.get("topic_title") or t.get("name", "")
            mdate = t.get("modified", "")[:10]
            label = f"📝 {mdate}"
            if person:
                label += f" / {person}"
            label += f" / {topic}"
            st.markdown(f"- {label}")
    if gc.is_authed("work"):
        st.caption(f"仕事メール {len(recent_emails)}件")
    else:
        st.info("仕事用メール未連携")
        if st.button("📧 仕事用メールを連携"):
            try:
                gc.connect_work_email(); load_work_emails.clear()
                st.success("連携しました"); st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"失敗: {e}")
