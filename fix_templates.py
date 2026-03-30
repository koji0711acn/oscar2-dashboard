"""Fix Templates: Generate structured fix instructions for Agent Teams based on QA failures."""

import json


def generate_fix_instruction(failures, warnings=None):
    """Generate a combined fix instruction from a list of failures.

    Args:
        failures: List of failure dicts from output_verifier
        warnings: Optional list of warning dicts

    Returns:
        str: Fix instruction text to pass to Claude Code CLI
    """
    if not failures:
        return ""

    lines = [
        "以下の品質問題が検出されました。全て修正してください。",
        "修正後、同じ問題が再発しないことを確認してからcommitしてください。",
        "",
    ]

    for i, f in enumerate(failures, 1):
        ftype = f.get("type", "unknown")
        template_func = _TEMPLATES.get(ftype, _template_generic)
        lines.append(f"【問題{i}】")
        lines.append(template_func(f))
        lines.append("")

    if warnings:
        lines.append("--- 警告（可能であれば修正） ---")
        for w in warnings:
            lines.append(f"- {w.get('type', 'warning')}: {w.get('detail', '')}")
        lines.append("")

    return "\n".join(lines)


def _template_irrelevant_sns(failure):
    snippet = failure.get("snippet", "")
    return f"""記事HTML内に記事テーマと無関係なSNS投稿が埋め込まれています。
無関係な投稿: "{snippet}"
対処:
1. 記事HTML内の無関係なblockquote（twitter-tweet）を除去
2. 記事テーマに関連するSNS投稿のみを残す
3. 修正後、全てのblockquoteがテーマに関連していることを確認"""


def _template_text_duplication(failure):
    snippet = failure.get("snippet", "")
    count = "複数"
    detail = failure.get("detail", "")
    if "repeated" in detail:
        try:
            count = detail.split("repeated")[1].split("times")[0].strip()
        except Exception:
            pass
    return f"""記事内で同じ文が{count}回繰り返されています。
重複箇所: "{snippet}"
対処:
1. 各セクションの役割を明確にし、同じ結論を繰り返さない
2. 重複する文を削除し、異なる角度からの説明に書き換える"""


def _template_incorrect_fact(failure):
    detail = failure.get("detail", "")
    return f"""ファクトチェックで事実誤りが検出されました。
詳細: {detail}
対処:
1. 該当箇所の事実関係を再調査
2. 正しい情報に修正
3. 修正後のファクトチェックを再実行"""


def _template_html_error(failure):
    detail = failure.get("detail", "")
    return f"""HTMLの構文エラーがあります。
エラー: {detail}
対処:
1. タグの閉じ忘れや入れ子のミスを修正
2. HTML構文チェッカーで検証"""


def _template_not_publishable(failure):
    reasons = failure.get("reasons", "理由不明")
    return f"""品質判定でneeds_revision（要修正）と判定されました。
理由: {reasons}
対処:
1. 上記の理由に対処して修正
2. publishable=trueになるまで品質を改善"""


def _template_generic(failure):
    detail = failure.get("detail", "")
    snippet = failure.get("snippet", "")
    return f"""品質問題が検出されました。
詳細: {detail}
{f'箇所: "{snippet}"' if snippet else ''}
対処: 該当箇所を修正してください。"""


_TEMPLATES = {
    "irrelevant_sns": _template_irrelevant_sns,
    "text_duplication": _template_text_duplication,
    "incorrect_fact": _template_incorrect_fact,
    "html_error": _template_html_error,
    "not_publishable": _template_not_publishable,
}
