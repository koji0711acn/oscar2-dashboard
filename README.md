# OSCAR2 - Agent Teams Monitoring & Recovery System

Agent Teams の監視・復旧・可視化基盤。

複数の Claude Code Agent Teams プロジェクトを常時監視し、中断検知 -> 自動復旧、Web ダッシュボードでの状態表示、人間への通知を行う常駐 Python プロセスです。

## 主な機能

- **プロセス監視**: Claude Code プロセスの生死・停滞を60秒間隔で検知
- **自動復旧**: DEAD/STALLED 状態を検知して自動再起動 (Recovery Orchestrator)
- **タスクキュー**: バッチを優先度付きキューで管理、完了後に自動で次のバッチを起動
- **AI タスク分解**: Anthropic API (Claude) で曖昧な依頼を構造化されたchild taskに分解
- **品質管理**: Strategic Judge による publishable 判定、コスト上限管理
- **Web ダッシュボード**: 5タブ構成のリアルタイム監視UI (Flask)
- **通知**: Windows デスクトップ通知 + 通知履歴 (LINE/Slack拡張可能)
- **Railway デプロイ対応**: Procfile + gunicorn でクラウドデプロイ可能

## セットアップ

### 1. 依存関係インストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env` ファイルをプロジェクト直下またはblog_automation配下に作成:

```
ANTHROPIC_API_KEY=sk-ant-...
DASHBOARD_TOKEN=your-secret-token
```

### 3. config.json の設定

```json
{
  "projects": [
    {
      "id": "blog_automation",
      "name": "Project Name",
      "path": "blog_automation",
      "auto_restart": true,
      "stall_timeout_minutes": 30,
      "max_cost_per_day_usd": 10
    }
  ],
  "oscar": {
    "dashboard_port": 5001,
    "check_interval_seconds": 60,
    "base_path": "C:\\Users\\user\\projects",
    "dashboard_token": null,
    "localhost_no_auth": true,
    "remote_dashboard_url": null
  }
}
```

#### config.json フィールド説明

**projects[] (プロジェクト設定)**:
| フィールド | 説明 | デフォルト |
|---|---|---|
| `id` | 一意のプロジェクトID | (必須) |
| `name` | 表示名 | (必須) |
| `path` | base_pathからの相対パス | (必須) |
| `auto_restart` | 自動再起動の有効/無効 | true |
| `stall_timeout_minutes` | 停滞と判定するまでの分数 | 30 |
| `max_cost_per_day_usd` | 1日のコスト上限 (USD) | 10 |

**oscar (グローバル設定)**:
| フィールド | 説明 | デフォルト |
|---|---|---|
| `dashboard_port` | ダッシュボードのポート | 5001 |
| `check_interval_seconds` | 監視ループの間隔 (秒) | 60 |
| `base_path` | プロジェクトのベースディレクトリ | (必須) |
| `dashboard_token` | API認証トークン (null=認証なし) | null |
| `localhost_no_auth` | ローカルアクセス時に認証不要 | true |
| `remote_dashboard_url` | リモートダッシュボードURL (heartbeat送信先) | null |

## 使い方

### 監視デーモン起動

```bash
python oscar_core.py
```

60秒間隔で全プロジェクトを監視し、DEAD/STALLED を検知すると自動で復旧します。

### Web ダッシュボード起動

```bash
python dashboard.py
```

ブラウザで http://localhost:5001 にアクセス。

### ダッシュボード 5タブ構成

1. **Home**: プロジェクトカード (ステータス、PID、キュー情報、最新コミット) + プロジェクト管理 + イベントログ
2. **Task Queue**: バッチの追加・一覧・優先度変更・キャンセル・削除
3. **Task Decomposer**: AI チャットUI で依頼を構造化 -> キューに一括登録
4. **Analytics**: 日別コスト / publishable率 / イベント種別 / 作業時間のグラフ
5. **Notifications**: 通知履歴 + タイプフィルター

## プロジェクト追加

ダッシュボードの Home タブ > Project Management から追加可能。
または `config.json` の `projects` 配列に直接追加。

## Railwayデプロイ

詳細は [DEPLOY.md](DEPLOY.md) を参照。

```bash
railway up
```

## アーキテクチャ

| ファイル | 役割 |
|---|---|
| `oscar_core.py` | メイン監視ループ (60秒間隔) + タスク自動注入 |
| `process_monitor.py` | プロセス生死・停滞検知 (psutil / wmic) |
| `cli_controller.py` | Claude CLI 起動・停止・再起動 |
| `recovery_orchestrator.py` | 復旧判定エンジン (CONTINUE/RESTART/RETRY/ESCALATE/PAUSE/ABORT) |
| `quality_gate.py` | Mechanical Judge + Strategic Judge |
| `task_backlog.py` | SQLite ベースのタスクキュー管理 |
| `task_decomposer.py` | OpenAI API (gpt-4o) でタスク分解 |
| `output_verifier.py` | 成果物の自動品質検証 (SNS関連性、重複、HTML) |
| `fix_templates.py` | 品質問題ごとの修正指示テンプレート |
| `qa_checker.py` | 記事/LP の品質チェック CLI ツール |
| `orchestrator.py` | Playwright: 参謀チャット ↔ Claude Code 自動中継 |
| `notifier.py` | 通知システム (Desktop/Slack/LINE 基底クラス設計) |
| `dashboard.py` | Flask Web ダッシュボード (port 5001) + 全API |
| `models.py` | SQLite 永続化 (project_state, event_log, cost_record, etc.) |
| `config.json` | プロジェクト定義と OSCAR 設定 |

## Orchestrator (参謀 ↔ Claude Code 自動中継)

Playwright を使って claude.ai の参謀チャットと Claude Code GUI の間でメッセージを自動中継する常駐プログラム。

### セットアップ

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 設定

`orchestrator_config.json` を編集:

```json
{
  "advisor_chat_url": "https://claude.ai/chat/YOUR_ADVISOR_CHAT_ID",
  "claude_code_url": "https://claude.ai/code",
  "chrome_profile_path": "C:\\Users\\koji3\\AppData\\Local\\Google\\Chrome\\User Data"
}
```

### 起動

```bash
# 通常起動
python orchestrator.py

# テストモード（ブラウザ動作確認）
python orchestrator.py --test

# バッチファイルで起動（Windows）
start_orchestrator.bat
```

### 注意事項

- Chrome が起動中の場合はプロファイル競合が発生します。既存の Chrome を閉じてから起動してください
- claude.ai にログイン済みの Chrome プロファイルを使用します
- Ctrl+C で停止
- ログは `logs/orchestrator_YYYYMMDD.log` に出力されます

## キーコマンド

```bash
# 監視開始
python oscar_core.py

# ダッシュボード起動
python dashboard.py

# Orchestrator起動
python orchestrator.py

# 依存関係インストール
pip install -r requirements.txt
python -m playwright install chromium
```
