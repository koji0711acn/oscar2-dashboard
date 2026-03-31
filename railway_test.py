# -*- coding: utf-8 -*-
"""Railway automated test script.
Generates an article on Railway and validates the output.

Usage:
    python railway_test.py                          # default keyword
    python railway_test.py "鬼束ちひろ 結婚"        # custom keyword
    python railway_test.py --url http://localhost:5000  # test local

Requires TEST_SECRET env var on Railway side.
"""
import requests
import json
import re
import sys
import os

RAILWAY_URL = os.environ.get("RAILWAY_URL", "https://web-production-35828.up.railway.app")
TEST_SECRET = os.environ.get("TEST_SECRET", "blogauto_test_2026secure")


def test_generate(keyword, topic=None, base_url=None):
    base_url = base_url or RAILWAY_URL
    topic = topic or keyword
    print(f"=== Railway Test: {keyword} ===")
    print(f"URL: {base_url}/api/test-generate")
    print(f"Requesting article generation...")

    try:
        resp = requests.post(
            f"{base_url}/api/test-generate",
            headers={"X-Test-Secret": TEST_SECRET, "Content-Type": "application/json"},
            json={"keyword": keyword, "topic": topic},
            timeout=600,
        )
    except requests.exceptions.Timeout:
        print("ERROR: Request timed out (10 min)")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR: Connection failed: {e}")
        return None

    if resp.status_code != 200:
        print(f"ERROR: HTTP {resp.status_code}")
        print(resp.text[:1000])
        return None

    data = resp.json()
    if data.get("status") == "error":
        print(f"ERROR: {data.get('error', 'unknown')}")
        print(data.get("traceback", "")[:1000])
        return None

    # Extract results
    html = data.get("article_html", "")
    debug = data.get("debug_packet", {})
    sns_debug = debug.get("sns_debug", {})

    # Count forbidden elements
    visual_cards = len(re.findall(r'class="visual-card"', html, re.IGNORECASE))
    blockquotes = re.findall(r'<blockquote[^>]*>', html, re.IGNORECASE)
    figures = len(re.findall(r'<figure', html, re.IGNORECASE))
    iframes = len(re.findall(r'<iframe', html, re.IGNORECASE))

    # SNS embed blockquotes (from selector pipeline - these are OK)
    sns_blockquotes = len(re.findall(r'<blockquote[^>]*class="twitter-tweet"', html, re.IGNORECASE))

    results = {
        "keyword": keyword,
        "status": "success",
        "publishable": data.get("publishable"),
        "seo_title": data.get("seo_title", ""),
        "exec_mode": data.get("exec_mode", ""),
        "html_length": len(html),
        "visual_card_count": visual_cards,
        "total_blockquote_count": len(blockquotes),
        "sns_blockquote_count": sns_blockquotes,
        "figure_count": figures,
        "iframe_count": iframes,
        "llm_blockquote_stripped": debug.get("llm_blockquote_stripped", "N/A"),
        "llm_visual_card_stripped": debug.get("llm_visual_card_stripped", "N/A"),
        "llm_figure_stripped": debug.get("llm_figure_stripped", "N/A"),
        "llm_iframe_stripped": debug.get("llm_iframe_stripped", "N/A"),
        "sns_candidate_count": sns_debug.get("candidate_count", "N/A"),
        "sns_adopted_count": sns_debug.get("adopted_count", "N/A"),
        "sns_embed_count": sns_debug.get("final_embed_count", "N/A"),
        "thumbnail_url": data.get("thumbnail_url", ""),
        "thumbnail_exists": bool(data.get("thumbnail_url")),
        "cost": data.get("cost", {}),
    }

    # Validation
    issues = []
    if visual_cards > 0:
        issues.append(f"FAIL: visual-card {visual_cards}件が残っている")
    if figures > 0:
        issues.append(f"FAIL: figure {figures}件が残っている")
    if iframes > 0:
        issues.append(f"FAIL: iframe {iframes}件が残っている")
    if not data.get("publishable"):
        issues.append(f"WARN: publishable=false")
    if not html:
        issues.append("FAIL: article_html is empty")
    if not data.get("thumbnail_url"):
        issues.append("WARN: no thumbnail generated")

    results["issues"] = issues
    results["pass"] = all(not i.startswith("FAIL") for i in issues)

    # Output
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # Save results
    out_file = f"_railway_test_{keyword.replace(' ', '_')}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {out_file}")

    if results["pass"]:
        print("\n✓ ALL CHECKS PASSED")
    else:
        print(f"\n✗ {len([i for i in issues if i.startswith('FAIL')])} FAILURES, {len([i for i in issues if i.startswith('WARN')])} WARNINGS")

    return results


if __name__ == "__main__":
    url = None
    kw = "鬼束ちひろ 結婚"

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--url" and i + 1 < len(args):
            url = args[i + 1]
        elif not arg.startswith("--"):
            kw = arg

    test_generate(kw, kw, base_url=url)
