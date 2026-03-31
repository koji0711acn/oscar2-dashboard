# OSCAR2 Railway Deploy Guide

## 前提条件

1. Railway アカウント: https://railway.app/
2. Railway CLI インストール:
   ```bash
   npm install -g @railway/cli
   ```
3. Railway CLI ログイン:
   ```bash
   railway login
   ```

## デプロイ手順

### 1. Railway プロジェクト作成

```bash
cd C:\Users\koji3\OneDrive\デスクトップ\oscar2
railway init
```

プロジェクト名を聞かれたら `oscar2-dashboard` と入力。

### 2. 環境変数の設定

Railway ダッシュボード (https://railway.app/dashboard) で、または CLI で設定:

```bash
# 認証トークン（任意のパスワードを設定）
railway variables set DASHBOARD_TOKEN=your-secret-token-here

# Anthropic API キー（.env から値をコピー）
railway variables set ANTHROPIC_API_KEY=sk-ant-api03-xxxxx

# ポート（Railway が自動設定するため通常不要）
# railway variables set PORT=5001
```

### 3. デプロイ実行

```bash
railway up
```

初回は数分かかります。完了すると URL が表示されます。

### 4. デプロイ URL の確認

```bash
railway open
```

または Railway ダッシュボードの Settings > Domains で確認。
例: `oscar2-dashboard-production.up.railway.app`

### 5. カスタムドメイン設定（オプション）

Railway ダッシュボード > Settings > Domains > Add Custom Domain

## ローカル oscar_core.py からの接続設定

### config.json に remote_dashboard_url を設定

```json
{
  "oscar": {
    "remote_dashboard_url": "https://oscar2-dashboard-production.up.railway.app",
    "dashboard_token": "your-secret-token-here"
  }
}
```

設定すると、oscar_core.py が60秒ごとにheartbeatを Railway ダッシュボードに送信します。

### ローカルでの起動

```bash
# ローカル監視デーモン（常時起動）
python oscar_core.py

# ローカルダッシュボード（オプション、Railway版と併用可能）
python dashboard.py
```

## ファイル構成（デプロイ対象）

```
oscar2/
  dashboard.py       # Flask app (gunicorn で起動)
  models.py          # SQLite DB
  task_backlog.py    # タスクキュー
  task_decomposer.py # AI タスク分解
  notifier.py        # 通知
  cli_controller.py  # CLI制御（Railway上では使わない）
  oscar_core.py      # 監視ループ（ローカル専用）
  config.json        # 設定
  requirements.txt   # 依存関係
  Procfile           # gunicorn 起動設定
  railway.json       # Railway 設定
  templates/
    dashboard.html   # ダッシュボードUI
```

## 注意事項

### SQLite について
- Railway はエフェメラルストレージのため、デプロイ毎にDBがリセットされます
- 永続化が必要な場合: Railway Volume を追加するか、PostgreSQL に移行
- heartbeat 機能を使えば、ローカルの oscar_core.py からリアルタイムデータを受信できます

### セキュリティ
- `DASHBOARD_TOKEN` を必ず設定してください（未設定だと認証なしでアクセス可能）
- ローカルからの heartbeat には同じトークンが必要です

### プロセス監視について
- Railway 上のダッシュボードはビューア専用です
- 実際のプロセス監視・復旧は **ローカルの oscar_core.py** が行います
- Railway ダッシュボードは heartbeat API でローカルからデータを受信して表示します

## トラブルシューティング

### デプロイが失敗する
```bash
# ログ確認
railway logs

# 再デプロイ
railway up
```

### ダッシュボードにアクセスできない
1. Railway ダッシュボード > Deployments で最新デプロイのステータスを確認
2. Settings > Domains でURLが設定されているか確認
3. `railway logs` でエラーを確認

### heartbeat が届かない
1. config.json の `remote_dashboard_url` が正しいか確認
2. `dashboard_token` がローカルとRailwayで一致しているか確認
3. oscar_core.py のログに `Heartbeat send failed` がないか確認

### API で 401 Unauthorized が返る
1. `DASHBOARD_TOKEN` 環境変数が設定されているか確認
2. リクエストに `Authorization: Bearer <token>` ヘッダーを付けているか確認
3. ローカルからのアクセスで `localhost_no_auth: true` が設定されているか確認

## 環境変数一覧

| 変数名 | 説明 | 必須 |
|---|---|---|
| `DASHBOARD_PASSWORD` | ダッシュボードログインパスワード | **必須** (設定しないとログイン不要でアクセス可能) |
| `SECRET_KEY` | Flask セッション暗号鍵 (任意の長い文字列) | **必須** |
| `DASHBOARD_TOKEN` | API認証トークン (Bearer header用) | 推奨 |
| `OPENAI_API_KEY` | OpenAI API キー (Task Decomposer gpt-4o用) | オプション |
| `PORT` | サーバーポート (Railway自動設定) | 不要 |
| `OSCAR_MODE` | `cloud` を設定するとプロセス監視を無効化 | オプション |
| `DATABASE_PATH` | SQLiteファイルのパス | オプション |

### Railway環境変数の設定方法

```bash
# 必須: ダッシュボードパスワード
railway variables set DASHBOARD_PASSWORD=your-secure-password

# 必須: Flaskセッション鍵
railway variables set SECRET_KEY=your-random-secret-key-here

# 推奨: API認証トークン
railway variables set DASHBOARD_TOKEN=your-api-token

# オプション: OpenAI API (Task Decomposer用)
railway variables set OPENAI_API_KEY=sk-...

# オプション: クラウドモード（プロセス監視無効化）
railway variables set OSCAR_MODE=cloud
```
