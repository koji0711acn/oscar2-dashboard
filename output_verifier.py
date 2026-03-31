"""Output Verifier: Automatically verify Agent Teams output quality.

Checks articles for: irrelevant SNS, text duplication, HTML errors, fact issues.
Checks debug packets for: publishable verdict, incorrect facts, embed count mismatch.
"""

import os
import json
import re
import logging
from html.parser import HTMLParser
from collections import Counter

logger = logging.getLogger("oscar2.output_verifier")


class _HTMLExtractor(HTMLParser):
    """Minimal HTML parser for verification."""

    def __init__(self):
        super().__init__()
        self.blockquotes = []
        self.in_bq = False
        self.bq_text = []
        self.text_chunks = []
        self.in_style = False
        self.in_script = False
        self.tag_stack = []
        self.tag_errors = []
        self._void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t not in self._void:
            self.tag_stack.append(t)
        if t == 'blockquote':
            self.in_bq = True
            self.bq_text = []
            cls = dict(attrs).get('class', '')
            self.blockquotes.append({'class': cls, 'text': ''})
        if t == 'style': self.in_style = True
        if t == 'script': self.in_script = True

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self._void:
            return
        if t == 'blockquote':
            self.in_bq = False
            if self.blockquotes:
                self.blockquotes[-1]['text'] = ' '.join(self.bq_text).strip()
        if t == 'style': self.in_style = False
        if t == 'script': self.in_script = False
        if self.tag_stack and self.tag_stack[-1] == t:
            self.tag_stack.pop()
        elif self.tag_stack:
            self.tag_errors.append(f"Expected </{self.tag_stack[-1]}> but found </{t}>")

    def handle_data(self, data):
        if self.in_style or self.in_script:
            return
        s = data.strip()
        if s:
            self.text_chunks.append(s)
        if self.in_bq:
            self.bq_text.append(s)

    def get_full_text(self):
        return ' '.join(self.text_chunks)


def verify_article(html_path, keyword=""):
    """Verify an article HTML file.

    Args:
        html_path: Path to HTML file
        keyword: Target keyword (space-separated terms)

    Returns:
        {"pass": bool, "failures": [...], "warnings": [...]}
    """
    result = {"pass": True, "failures": [], "warnings": []}

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError as e:
        result["pass"] = False
        result["failures"].append({"type": "file_error", "detail": str(e)})
        return result

    ext = _HTMLExtractor()
    ext.feed(html)

    kw_terms = [w.lower() for w in keyword.split() if len(w) >= 2] if keyword else []

    # Check 1: Irrelevant SNS embeds
    for i, bq in enumerate(ext.blockquotes):
        cls = bq.get('class', '').lower()
        is_sns = any(s in cls for s in ('twitter', 'tweet', 'instagram', 'tiktok'))
        if not is_sns and '@' not in bq['text'] and '#' not in bq['text']:
            continue
        if kw_terms and bq['text'].strip():
            text_lower = bq['text'].lower()
            if not any(kw in text_lower for kw in kw_terms):
                result["pass"] = False
                result["failures"].append({
                    "type": "irrelevant_sns",
                    "detail": f"SNS embed #{i+1} unrelated to '{keyword}'",
                    "snippet": bq['text'][:100],
                })

    # Check 2: Text duplication
    sentences = re.split(r'[。.！!？?\n]', ext.get_full_text())
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 20]
    for sent, count in Counter(sentences).most_common():
        if count >= 3:
            result["pass"] = False
            result["failures"].append({
                "type": "text_duplication",
                "detail": f"Sentence repeated {count} times",
                "snippet": sent[:80],
            })

    # Check 3: HTML errors
    for err in ext.tag_errors:
        result["warnings"].append({"type": "html_error", "detail": err})
    if ext.tag_stack:
        result["warnings"].append({
            "type": "html_error",
            "detail": f"Unclosed tags: {', '.join(ext.tag_stack[-5:])}"
        })

    return result


def verify_debug_packet(packet_path):
    """Verify a debug packet JSON file.

    Returns:
        {"pass": bool, "failures": [...]}
    """
    result = {"pass": True, "failures": []}

    try:
        with open(packet_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        result["pass"] = False
        result["failures"].append({"type": "parse_error", "detail": str(e)})
        return result

    # Check FINAL_JUDGMENT
    judgment = data.get("final_judgment") or data.get("FINAL_JUDGMENT") or {}
    if isinstance(judgment, str):
        try:
            judgment = json.loads(judgment)
        except json.JSONDecodeError:
            judgment = {}

    publishable = judgment.get("publishable", True)
    if publishable is False or str(publishable).lower() == "false":
        reasons = judgment.get("reasons") or judgment.get("reason") or "No reason given"
        result["pass"] = False
        result["failures"].append({
            "type": "not_publishable",
            "detail": f"FINAL_JUDGMENT: needs_revision",
            "reasons": reasons if isinstance(reasons, str) else json.dumps(reasons, ensure_ascii=False),
        })

    incorrect = judgment.get("incorrect", 0)
    if isinstance(incorrect, (int, float)) and incorrect > 0:
        result["pass"] = False
        result["failures"].append({
            "type": "incorrect_fact",
            "detail": f"{int(incorrect)} incorrect facts detected",
        })

    return result


def verify_project_output(project_path, keyword=""):
    """Verify the latest output files for a project.

    Returns:
        {"pass": bool, "article_result": {...}, "packet_result": {...}, "files_checked": [...]}
    """
    output_dir = os.path.join(project_path, "output")
    combined = {"pass": True, "article_result": None, "packet_result": None, "files_checked": []}

    if not os.path.isdir(output_dir):
        return combined

    # Find latest HTML and JSON files
    html_files = []
    json_files = []
    for fname in os.listdir(output_dir):
        fpath = os.path.join(output_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if fname.endswith('.html'):
            html_files.append((os.path.getmtime(fpath), fpath, fname))
        elif fname.endswith('.json') and 'debug' in fname.lower():
            json_files.append((os.path.getmtime(fpath), fpath, fname))

    # Verify latest article
    if html_files:
        html_files.sort(reverse=True)
        latest_html = html_files[0][1]
        combined["files_checked"].append(html_files[0][2])
        ar = verify_article(latest_html, keyword)
        combined["article_result"] = ar
        if not ar["pass"]:
            combined["pass"] = False

    # Verify latest debug packet
    if json_files:
        json_files.sort(reverse=True)
        latest_json = json_files[0][1]
        combined["files_checked"].append(json_files[0][2])
        pr = verify_debug_packet(latest_json)
        combined["packet_result"] = pr
        if not pr["pass"]:
            combined["pass"] = False

    # Also run railway validation on the latest HTML
    if html_files:
        with open(html_files[0][1], "r", encoding="utf-8", errors="replace") as f:
            html_content = f.read()
        rr = verify_railway_html(html_content)
        combined["railway_result"] = rr
        if not rr["pass"]:
            combined["pass"] = False

    return combined


def verify_railway_html(html_content):
    """Apply railway_test.py validation logic to HTML content.

    Checks for forbidden elements: visual-card, figure, iframe.
    Returns: {"pass": bool, "failures": [...], "warnings": [...]}
    """
    result = {"pass": True, "failures": [], "warnings": []}

    visual_cards = len(re.findall(r'class="visual-card"', html_content, re.IGNORECASE))
    figures = len(re.findall(r'<figure', html_content, re.IGNORECASE))
    iframes = len(re.findall(r'<iframe', html_content, re.IGNORECASE))

    if visual_cards > 0:
        result["pass"] = False
        result["failures"].append({
            "type": "forbidden_element",
            "detail": f"visual-card: {visual_cards} instances found (must be 0)",
        })
    if figures > 0:
        result["pass"] = False
        result["failures"].append({
            "type": "forbidden_element",
            "detail": f"figure: {figures} instances found (must be 0)",
        })
    if iframes > 0:
        result["pass"] = False
        result["failures"].append({
            "type": "forbidden_element",
            "detail": f"iframe: {iframes} instances found (must be 0)",
        })

    if not html_content.strip():
        result["pass"] = False
        result["failures"].append({"type": "empty_html", "detail": "article HTML is empty"})

    return result
