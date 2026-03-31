"""
loop_monitor.py — 参謀連携ループの外部監視+自動回復
"""
import os
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

SHARED_DIR = Path(r"C:\Users\koji3\shared-advisor")
BLOG_DIR = Path(r"C:\Users\koji3\OneDrive\デスクトップ\blog_automation")
CHECK_INTERVAL = 60  # 60秒ごとにチェック
STALL_THRESHOLD_MINUTES = 15  # 15分間変化がなければ滞りと判定
LOG_FILE = Path(r"C:\Users\koji3\OneDrive\デスクトップ\oscar2\logs\loop_monitor.log")

def log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{level}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_mtime(filepath):
    """ファイルの最終更新時刻を返す。存在しなければNone"""
    try:
        return datetime.fromtimestamp(filepath.stat().st_mtime)
    except FileNotFoundError:
        return None

def check_claude_code_running():
    """Claude Codeのプロセスが生きているか確認"""
    try:
        result = subprocess.run(
            ["tasklist"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        output = result.stdout.lower()
        return "claude.exe" in output or "node.exe" in output or "claude.cmd" in output
    except Exception:
        return False

def diagnose():
    """ループの状態を診断する"""
    question_md = SHARED_DIR / "question.md"
    answer_md = SHARED_DIR / "answer.md"
    question_flag = SHARED_DIR / "question_ready.flag"
    answer_flag = SHARED_DIR / "answer_ready.flag"

    q_mtime = get_mtime(question_md)
    a_mtime = get_mtime(answer_md)
    q_flag_exists = question_flag.exists()
    a_flag_exists = answer_flag.exists()
    claude_running = check_claude_code_running()

    now = datetime.now()
    stall_threshold = now - timedelta(minutes=STALL_THRESHOLD_MINUTES)

    # 状態判定
    if q_flag_exists:
        # Claude Codeが質問を書いたがCoworkが読みに来ていない
        if q_mtime and q_mtime < stall_threshold:
            return "COWORK_STALLED", "question_ready.flagが15分以上放置。Coworkが監視していない可能性"
        return "WAITING_COWORK", "Coworkがquestion.mdを読みに来るのを待機中"

    if a_flag_exists:
        # 参謀の回答があるがClaude Codeが読みに来ていない
        if a_mtime and a_mtime < stall_threshold:
            return "CODE_STALLED", "answer_ready.flagが15分以上放置。Claude Codeがanswer.mdを読んでいない"
        return "WAITING_CODE", "Claude Codeがanswer.mdを読むのを待機中"

    # どちらのflagもない
    if q_mtime and a_mtime:
        latest = max(q_mtime, a_mtime)
        if latest < stall_threshold:
            if not claude_running:
                return "CODE_DEAD", "Claude Codeプロセスが見つからない。再起動が必要"
            return "LOOP_STALLED", "flagなし、ファイルも15分以上更新なし。ループが停止している可能性"
        return "WORKING", "作業中（最終更新から15分以内）"

    return "UNKNOWN", "状態判定不能"

def recover(status, detail):
    """状態に応じた回復処理"""
    log(f"回復処理開始: {status} — {detail}", "WARNING")

    if status == "COWORK_STALLED":
        # Coworkが読みに来ていない → flagを再作成して催促
        log("対処: question_ready.flagを再作成")
        (SHARED_DIR / "question_ready.flag").touch()
        # さらに、参謀チャットに直接報告するために
        # question.mdの内容をコンソールに表示
        qmd = SHARED_DIR / "question.md"
        if qmd.exists():
            content = qmd.read_text(encoding="utf-8")[:500]
            log(f"question.mdの先頭: {content}")
        log("Coworkが反応しない場合、手動で参謀チャットにquestion.mdの内容を貼り付けてください")

    elif status == "CODE_STALLED":
        # Claude Codeがanswer.mdを読みに行っていない
        log("対処: Claude Codeに催促を送信（claude -p）")
        try:
            claude_cmd = r'C:\Users\koji3\AppData\Roaming\npm\claude.cmd'
            result = subprocess.run(
                [claude_cmd, "-p",
                 "C:\\Users\\koji3\\shared-advisor\\answer.md に参謀からの新しい指示が書かれています。読み取って指示に従ってください。完了したら結果をquestion.mdに書いてquestion_ready.flagを立ててください。",
                 "--dangerously-skip-permissions"],
                cwd=str(BLOG_DIR),
                capture_output=True, text=True, encoding="utf-8",
                timeout=600
            )
            log(f"Claude Code応答: {result.stdout[:300]}")
            # 結果をquestion.mdに書き込む
            if result.stdout.strip():
                (SHARED_DIR / "question.md").write_text(result.stdout, encoding="utf-8")
                (SHARED_DIR / "question_ready.flag").touch()
                log("question.mdを更新し、flagを立てました")
                # answer_ready.flagを削除
                aflag = SHARED_DIR / "answer_ready.flag"
                if aflag.exists():
                    aflag.unlink()
        except subprocess.TimeoutExpired:
            log("Claude Code応答タイムアウト（10分）", "ERROR")
        except Exception as e:
            log(f"Claude Code実行エラー: {e}", "ERROR")

    elif status == "CODE_DEAD":
        log("対処: Claude Codeプロセスが死んでいる。claude -pで直接実行")
        # CODE_STALLEDと同じ処理
        recover("CODE_STALLED", detail)

    elif status == "LOOP_STALLED":
        # 完全に止まっている。answer.mdの最新内容で再実行
        log("対処: ループ完全停止。answer.mdの指示でclaude -pを再実行")
        amd = SHARED_DIR / "answer.md"
        if amd.exists():
            recover("CODE_STALLED", detail)
        else:
            log("answer.mdが存在しない。参謀からの指示が必要です", "ERROR")

def main():
    log("=== Loop Monitor 起動 ===")
    log(f"監視対象: {SHARED_DIR}")
    log(f"チェック間隔: {CHECK_INTERVAL}秒、滞り閾値: {STALL_THRESHOLD_MINUTES}分")

    consecutive_stalls = 0

    while True:
        try:
            status, detail = diagnose()

            if status in ("WORKING", "WAITING_COWORK", "WAITING_CODE"):
                if consecutive_stalls > 0:
                    log(f"復旧確認: {status}")
                    consecutive_stalls = 0
                # 正常 or 待機中、何もしない
                log(f"状態: {status} — {detail}")

            elif status in ("COWORK_STALLED", "CODE_STALLED", "CODE_DEAD", "LOOP_STALLED"):
                consecutive_stalls += 1
                log(f"滞り検知 ({consecutive_stalls}回目): {status} — {detail}", "WARNING")

                if consecutive_stalls >= 2:
                    # 2回連続で滞りを検知したら回復処理
                    recover(status, detail)
                    consecutive_stalls = 0

            else:
                log(f"状態不明: {status} — {detail}")

        except Exception as e:
            log(f"監視ループエラー: {e}", "ERROR")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
