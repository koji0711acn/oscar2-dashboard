# OSCAR2

Agent Teams の監視・復旧・可視化基盤。

複数の Claude Code Agent Teams プロジェクトを常時監視し、中断検知→自動復旧、Web ダッシュボードでの状態表示、人間への通知を行う常駐 Python プロセスです。

## セットアップ

```bash
pip install -r requirements.txt
```

## 使い方

### 監視デーモン起動
```bash
python oscar_core.py
```

### Web ダッシュボード起動
```bash
python dashboard.py
```
ブラウザで http://localhost:5001 にアクセス。

## プロジェクト追加

`config.json` の `projects` 配列に追加:

```json
{
  "id": "my_project",
  "name": "プロジェクト名",
  "path": "my_project",
  "auto_restart": true,
  "stall_timeout_minutes": 30,
  "max_cost_per_day_usd": 10
}
```

## アーキテクチャ

| ファイル | 役割 |
|---|---|
| oscar_core.py | メイン監視ループ (60秒間隔) |
| process_monitor.py | プロセス生死・停滞検知 |
| cli_controller.py | Claude CLI 起動・停止・再起動 |
| quality_gate.py | Mechanical Judge ヘルスチェック |
| notifier.py | Windows デスクトップ通知 |
| dashboard.py | Flask Web ダッシュボード (port 5001) |
| models.py | SQLite 永続化 |
