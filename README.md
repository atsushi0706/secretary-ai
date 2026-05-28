# 🗒️ 秘書AI ── 朝夜ブリーフィング & タスク管理

Google カレンダー と Google ToDo(Google Tasks) を読み込み、
Gemini が **緊急度 × 重要度 × 所要時間** でタスクを自動仕分けする秘書AI。

- 🌅 **朝モード**：今日の予定＋未完了タスクから「今日の流れ」を提案
- 🌙 **夜モード**：音声入力した振り返りを踏まえ「明日の準備」を提案＆ToDo追加
- ✅ チェックを入れると **Google ToDo に完了が自動で書き戻し**
- 🗂️ アイゼンハワー・マトリクス(4ボックス)で見やすく整理

---

## 初回セットアップ

既存の `zoom-notebooklm-workflow` のGoogle設定を流用するので、**新しいGoogle Cloudプロジェクトは作りません**。`credentials.json` も配置済みです。残りはこれだけ：

### 1. Pythonライブラリを入れる
```powershell
cd "c:\Users\affec\claudecode『開発』\secretary-ai"
py -m pip install -r requirements.txt
```

### 2. Gemini APIキーを入れる（無料）
1. https://aistudio.google.com/apikey で「APIキーを作成」→ コピー
2. `.streamlit\secrets.toml` の `GEMINI_API_KEY` の値を、コピーしたキーに置き換える
   （`DRIVE_ROOT_FOLDER_ID` はZoom連携から引き継ぎ済み・変更不要）
3. 同ページでモデル名を確認し、無料枠が一番大きいFlash系を `GEMINI_MODEL` に。迷ったら既定の `gemini-2.5-flash` のまま

### 3. 既存プロジェクト(affection所有)にAPIを足す
※ 操作は **affectionのプロジェクト**で行う。仕事用アカウントで新規プロジェクトは作らない。
直リンクで開いて「有効にする」を押す：

- Calendar API → <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>
- Tasks API → <https://console.cloud.google.com/apis/library/tasks.googleapis.com>
- Gmail API → <https://console.cloud.google.com/apis/library/gmail.googleapis.com>
- Drive API … 既に有効

### 4. 仕事用アカウントをテストユーザーに追加
仕事用メール(`mental.tuning.online@gmail.com`)を読むので、affectionプロジェクトの
「OAuth同意画面」→「テストユーザー」に **affection と 仕事用Gmail の両方**を入れる。
（仕事用が無いと、後半のメール許可で弾かれる）

### 5. 起動して、2アカウントでログイン許可（初回だけ2回）
```powershell
streamlit run app.py
```
このアプリは2つのアカウントを使い分ける（パスワードは使わない・OAuthのみ）：

| アカウント | 読むもの | ログインで選ぶ |
|---|---|---|
| main = affection | 予定 / ToDo / Zoom文字起こし | 普段のaffectionアカウント |
| work = 仕事用 | メール(仕事の連絡) | mental.tuning.online@gmail.com |

- 起動すると **まず main 用のログイン**が開く → affectionを選んで許可
- 続いて **work 用のログイン**が開く → 仕事用アカウントを選んで許可
- 「確認されていないアプリ」警告は「詳細」→「(アプリ名)に移動」で進める（自分専用のため）
- 以降は `data\token_main.json` / `data\token_work.json` に保存され自動ログイン

> ⚠️ 仕事用が**会社のWorkspace**の場合、管理者が外部アプリを禁止していると
> `admin_policy_enforced` 等で失敗することがある。その時は管理者許可が必要。

---

## 使い方

| | やること |
|---|---|
| 朝 | 朝モードで「今日の流れを提案してもらう」→ 内容を見て一日を組み立てる |
| 日中 | 終わったタスクの □ にチェック → Google ToDo にも自動反映 |
| 夜 | 夜モードで振り返りを音声入力 → 明日のタスク＆優先順位を提案、ToDoに追加 |

---

## コストについて
- 既定 `gemini-2.5-flash-lite` は無料枠1,000回/日。1日数回の利用なら使い切れない。
- タスク分類は **キャッシュ**するので、内容が変わらない限りAPIを再度叩きません。

## スマホから使う（Streamlit Cloud 公開）

ローカルで動作確認できたら、Streamlit Community Cloud に公開してURLでアクセスできます。

### A. リポジトリを用意
1. このフォルダで `git init` → `.gitignore` に `credentials.json`, `.streamlit/secrets.toml`, `data/` を入れる（既に入っていない場合は追加）
2. GitHub に **Private** リポジトリを作って push する

### B. ローカルで一度ログインを終わらせる
`streamlit run app.py` で2アカウントのログインを済ませ、`data/token_main.json` と `data/token_work.json` を作っておく。

### C. Streamlit Cloud にデプロイ
1. <https://share.streamlit.io/> → 新規アプリ → そのリポジトリと `app.py` を選ぶ
2. アプリ作成後の `Settings → Secrets` に、`.streamlit/secrets.toml` の内容を貼る
3. さらに次のセクションを追記する（**ローカルの token JSON をそのままヒアドキュメントで貼る**）：

```toml
APP_PASSWORD = "好きな合言葉"       # 公開URLにアクセスする時の合言葉
NOTIFY_KEY    = "好きなランダム文字列"  # LINE通知用URLの安全鍵

[google_tokens]
main = '''<data/token_main.json の中身をそのままコピペ>'''
work = '''<data/token_work.json の中身をそのままコピペ>'''
```

### D. Google Cloud 側の調整
OAuth クライアントは**デスクトップアプリ**のままで動きます。Cloud 上では `st.secrets` から token を復元するので、追加のリダイレクトURI設定は不要です。token が期限切れになったら、ローカルで再度 `streamlit run app.py` してログインし直し、得た token を再び Secrets に貼り直してください（refresh_token があれば数ヶ月有効）。

---

## 通知（ntfy.sh — 「秘書から連絡が届いた」式）

カレンダーには予定として書き込まず、**スマホに「秘書からのメッセージ」として通知**を届ける方式。ntfy.sh の無料Push基盤を使うので、登録もチャネル作成も不要。

### 1. スマホに ntfy アプリを入れる（5分）
1. App Store / Play Store で **「ntfy」** を検索してインストール（無料・登録不要）
2. アプリを開いて「+」→ **Subscribe to topic** に、推測されにくいトピック名を入力（例: `kiyose-link-affection-9z3xq`）
3. これで購読完了

> ⚠️ トピック名はURLの一部になります。**推測されない長めの名前**にしてください（誰でも同じトピックを購読できるため）。

### 2. Streamlit Cloud の Secrets に追加
```toml
NTFY_TOPIC      = "kiyose-link-affection-9z3xq"   # 上で決めたトピック名
NTFY_CLICK_URL  = "https://<アプリ名>.streamlit.app/"  # 通知タップ時に開くURL
# NTFY_TOKEN    = "..."   # 自前ntfyサーバー or 予約済みトピックを使う時だけ
```

ローカルで試す場合は `.streamlit/secrets.toml` に同じ内容を入れる。

### 3. テスト送信
- 秘書AIのサイドバー「📲 スマホ通知（ntfy）」→「テスト通知（ping）」を押す
- もしくはターミナルで:
  ```powershell
  py notify_ntfy.py ping
  ```
- スマホに「✅ 清瀬リンクのテスト通知」が届けば成功

### 4. 朝夜の自動通知（任意・cron-job.org）
自動で「朝のブリーフィング」「夜のブリーフィング」を送りたい場合のみ:

<https://console.cron-job.org/> で次のジョブを2つ作る:

| 名前 | URL | 時刻 |
|---|---|---|
| 朝の秘書 | `https://<アプリ名>.streamlit.app/?notify=morning&key=<NOTIFY_KEY>` | 毎日 08:00 (JST) |
| 夜の秘書 | `https://<アプリ名>.streamlit.app/?notify=evening&key=<NOTIFY_KEY>` | 毎日 17:00 (JST) |

→ アクセスされるとアプリは ntfy に送って終了（UIは出ません）。同じ日に2回叩かれても二重送信されません。

> 自動化が要らないなら、秘書AIを開いて「📲 いまのブリーフィングを送る」ボタンを押すだけでもOKです。

---

## 進化した使い方（v2 機能）

### 会話から自動でタスク追加
- 朝モードで「今日は○○もやりたい」と話したあと、チャット下の **「✨ ここまでの会話を今日のタスクに反映」** を押す
- 秘書が会話から具体的アクションを抽出 → 緊急度/重要度/所要時間/期限を編集して「追加」
- Googleタスクに登録され、4象限ボードに即反映されます

### 夜の会話から「明日のタスク」を逆算生成
- 夜モードでは、開いた瞬間に **明日の予定から逆算したたたき台**が提示されます
- 「明日はこれをやりたい」と話したあと、**「📅 ここまでの会話から明日のタスクを作る」** を押す
- 期限が**明日**で自動入力された状態でGoogleタスクに追加されます

### 会話履歴は自動で残る
`data/secretary.db` (SQLite) に保存されるので、同じ日の朝/夜モードを開き直しても会話の続きから話せます。

---

## ファイル構成
```
secretary-ai/
├── app.py            # Streamlit画面(朝夜モード・4ボックス・タスク抽出UI)
├── google_client.py  # カレンダー&ToDo&Drive&Gmail（secrets 経由 token 対応）
├── classifier.py     # Geminiでタスク分類・会話抽出・ブリーフィング生成
├── storage.py        # SQLite(会話/抽出タスク/ブリーフィング/通知履歴)
├── notify_line.py    # LINE Messaging API Push
├── requirements.txt
├── credentials.json  # ←自分で配置(gitignore済)
├── .streamlit/
│   └── secrets.toml  # ←自分で作成(gitignore済)
└── data/             # token・キャッシュ・secretary.db (自動生成)
```
