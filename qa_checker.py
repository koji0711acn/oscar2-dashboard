#!/usr/bin/env python3
"""QA Checker: Automated quality gate for blog articles and landing pages.

Usage:
    python qa_checker.py article.html --keyword "鬼束ちひろ 結婚"
    python qa_checker.py lp.html --type lp
    python qa_checker.py article.html --keyword "keyword" --json
"""

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from collections import Counter


# ============================================================
# HTML Parser for structure extraction
# ============================================================

class HTMLContentExtractor(HTMLParser):
    """Extract structured content from HTML for QA analysis."""

    def __init__(self):
        super().__init__()
        self.text_chunks = []        # All text content
        self.current_text = []
        self.blockquotes = []        # SNS embeds (twitter, instagram, etc.)
        self.in_blockquote = False
        self.bq_text = []
        self.tags_stack = []         # For mismatched tag detection
        self.tag_errors = []
        self.images = []             # img tags
        self.links = []              # a href tags
        self.meta_tags = {}          # meta name -> content
        self.title = ""
        self.in_title = False
        self.in_style = False        # Skip style/script content
        self.in_script = False
        self.headings = []           # h1-h6
        self.in_heading = False
        self.heading_text = []
        self.has_viewport = False
        self.has_form = False
        self.og_tags = {}
        # LP specific
        self.buttons = []
        self.in_button = False
        self.button_text = []

        self._void_elements = {
            'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'
        }

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        tag_lower = tag.lower()

        if tag_lower not in self._void_elements:
            self.tags_stack.append(tag_lower)

        if tag_lower == 'blockquote':
            self.in_blockquote = True
            self.bq_text = []
            bq_class = attrs_dict.get('class', '')
            self.blockquotes.append({'class': bq_class, 'text': ''})

        if tag_lower == 'img':
            self.images.append({
                'src': attrs_dict.get('src', ''),
                'alt': attrs_dict.get('alt', None),
            })

        if tag_lower == 'a':
            self.links.append({
                'href': attrs_dict.get('href', ''),
                'text': '',
            })

        if tag_lower == 'meta':
            name = attrs_dict.get('name', '')
            prop = attrs_dict.get('property', '')
            content = attrs_dict.get('content', '')
            if name:
                self.meta_tags[name.lower()] = content
            if prop:
                if prop.startswith('og:'):
                    self.og_tags[prop] = content
            if name == 'viewport' or attrs_dict.get('name', '') == 'viewport':
                self.has_viewport = True

        if tag_lower == 'title':
            self.in_title = True

        if tag_lower == 'style':
            self.in_style = True
        if tag_lower == 'script':
            self.in_script = True

        if tag_lower in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.in_heading = True
            self.heading_text = []

        if tag_lower == 'form':
            self.has_form = True

        if tag_lower == 'button' or (tag_lower == 'a' and 'btn' in attrs_dict.get('class', '')):
            self.in_button = True
            self.button_text = []

        if tag_lower == 'input' and attrs_dict.get('type', '') == 'submit':
            self.buttons.append(attrs_dict.get('value', 'submit'))

    def handle_endtag(self, tag):
        tag_lower = tag.lower()

        if tag_lower in self._void_elements:
            return

        if self.tags_stack:
            expected = self.tags_stack[-1]
            if tag_lower == expected:
                self.tags_stack.pop()
            else:
                # Mismatched tag
                self.tag_errors.append(f"Expected </{expected}> but found </{tag_lower}>")
                # Try to find matching tag in stack
                for i in range(len(self.tags_stack) - 1, -1, -1):
                    if self.tags_stack[i] == tag_lower:
                        self.tags_stack = self.tags_stack[:i]
                        break

        if tag_lower == 'blockquote':
            self.in_blockquote = False
            if self.blockquotes:
                self.blockquotes[-1]['text'] = ' '.join(self.bq_text).strip()

        if tag_lower == 'title':
            self.in_title = False
        if tag_lower == 'style':
            self.in_style = False
        if tag_lower == 'script':
            self.in_script = False

        if tag_lower in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.in_heading = False
            self.headings.append({'level': tag_lower, 'text': ' '.join(self.heading_text).strip()})

        if tag_lower == 'button' or (tag_lower == 'a' and self.in_button):
            self.in_button = False
            txt = ' '.join(self.button_text).strip()
            if txt:
                self.buttons.append(txt)

    def handle_data(self, data):
        # Skip style/script content
        if self.in_style or self.in_script:
            return

        stripped = data.strip()
        if stripped:
            self.text_chunks.append(stripped)

        if self.in_blockquote:
            self.bq_text.append(data.strip())

        if self.in_title:
            self.title += data

        if self.in_heading:
            self.heading_text.append(data.strip())

        if self.in_button:
            self.button_text.append(data.strip())

        # Track link text
        if self.links and stripped:
            # Update last link's text
            self.links[-1]['text'] = stripped

    def get_full_text(self):
        return ' '.join(self.text_chunks)

    def get_body_text(self):
        """Get text excluding title and headings — for opening relevance check."""
        body_chunks = []
        heading_texts = {h['text'] for h in self.headings}
        for chunk in self.text_chunks:
            if chunk == self.title.strip():
                continue
            if chunk in heading_texts:
                continue
            body_chunks.append(chunk)
        return ' '.join(body_chunks)


# ============================================================
# Check functions for ARTICLE mode
# ============================================================

def check_sns_relevance(extractor, keywords):
    """Check if embedded SNS (blockquotes) are relevant to the article keywords."""
    issues = []
    if not keywords:
        return issues

    kw_set = set()
    for kw in keywords:
        kw_set.add(kw.lower())
        # Also add individual words for multi-word keywords
        for word in kw.split():
            if len(word) >= 2:
                kw_set.add(word.lower())

    for i, bq in enumerate(extractor.blockquotes):
        bq_class = bq.get('class', '').lower()
        # Only check SNS embeds (twitter, instagram, tiktok, etc.)
        is_sns = any(s in bq_class for s in ('twitter', 'instagram', 'tiktok', 'reddit'))
        if not is_sns and 'tweet' not in bq_class:
            # Also check if it looks like a tweet by content pattern
            if '@' not in bq['text'] and '#' not in bq['text']:
                continue

        text_lower = bq['text'].lower()
        # Check if any keyword appears in the blockquote text
        relevant = any(kw in text_lower for kw in kw_set)

        if not relevant and bq['text'].strip():
            preview = bq['text'][:100]
            issues.append({
                'type': 'irrelevant_sns',
                'severity': 'critical',
                'message': f'SNS embed #{i+1} is unrelated to keywords {keywords}',
                'quote': preview,
            })

    return issues


def check_text_duplication(extractor):
    """Check for excessive text duplication (same sentence 3+ times)."""
    issues = []
    full_text = extractor.get_full_text()

    # Split into sentences (Japanese and English)
    sentences = re.split(r'[。.！!？?\n]', full_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 20]

    counter = Counter(sentences)
    for sentence, count in counter.most_common():
        if count >= 3:
            issues.append({
                'type': 'text_duplication',
                'severity': 'critical',
                'message': f'Sentence repeated {count} times',
                'quote': sentence[:80],
            })

    return issues


def check_html_errors(extractor, raw_html):
    """Check for HTML structural errors."""
    issues = []

    for err in extractor.tag_errors:
        issues.append({
            'type': 'html_error',
            'severity': 'warning',
            'message': f'Mismatched HTML tag: {err}',
            'quote': '',
        })

    # Check for unclosed tags at end
    if extractor.tags_stack:
        unclosed = ', '.join(f'<{t}>' for t in extractor.tags_stack[-5:])
        issues.append({
            'type': 'html_error',
            'severity': 'warning',
            'message': f'Unclosed tags remaining: {unclosed}',
            'quote': '',
        })

    return issues


def check_opening_relevance(extractor, keywords):
    """Check if the opening 200 chars address the main keyword topic.

    The opening must contain ALL keywords (or their core words), not just one.
    For example, for keywords ["鬼束ちひろ", "結婚"], the opening must mention
    both "鬼束ちひろ" AND "結婚" to be considered relevant.
    """
    issues = []
    if not keywords:
        return issues

    body_text = extractor.get_body_text()
    opening = body_text[:300].lower()

    # Each keyword must be represented in the opening body text
    missing_keywords = []
    for kw in keywords:
        kw_lower = kw.lower()
        words = [w for w in kw.split() if len(w) >= 2]
        found = kw_lower in opening or any(w.lower() in opening for w in words)
        if not found:
            missing_keywords.append(kw)

    if missing_keywords:
        issues.append({
            'type': 'opening_irrelevant',
            'severity': 'critical',
            'message': f'Opening body text (300 chars) does not address: {missing_keywords}. '
                       f'The article should answer the reader\'s query immediately.',
            'quote': body_text[:150],
        })

    return issues


def check_images(extractor):
    """Check img tags have alt attributes."""
    issues = []
    for i, img in enumerate(extractor.images):
        if img['alt'] is None or img['alt'].strip() == '':
            issues.append({
                'type': 'missing_img_alt',
                'severity': 'warning',
                'message': f'Image #{i+1} missing alt attribute: src="{img["src"]}"',
                'quote': img['src'],
            })
    return issues


def check_links(extractor):
    """Check for placeholder/broken links."""
    issues = []
    for link in extractor.links:
        href = link.get('href', '')
        if href in ('#', '', 'javascript:void(0)', 'javascript:;'):
            issues.append({
                'type': 'placeholder_link',
                'severity': 'warning',
                'message': f'Placeholder link found: href="{href}" text="{link.get("text", "")}"',
                'quote': link.get('text', ''),
            })
    return issues


def check_meta_tags(extractor):
    """Check for essential meta tags."""
    issues = []
    if not extractor.title.strip():
        issues.append({
            'type': 'missing_meta',
            'severity': 'warning',
            'message': 'Missing or empty <title> tag',
            'quote': '',
        })
    if 'description' not in extractor.meta_tags:
        issues.append({
            'type': 'missing_meta',
            'severity': 'warning',
            'message': 'Missing meta description tag',
            'quote': '',
        })
    return issues


# ============================================================
# Check functions for LP mode
# ============================================================

def check_lp_responsive(extractor):
    """Check viewport meta for responsive design."""
    issues = []
    if not extractor.has_viewport:
        issues.append({
            'type': 'no_viewport',
            'severity': 'critical',
            'message': 'Missing viewport meta tag (not responsive)',
            'quote': '',
        })
    return issues


def check_lp_cta(extractor):
    """Check for CTA buttons."""
    issues = []
    cta_keywords = ['申込', '購入', '登録', '始める', '無料', 'start', 'buy', 'signup',
                    '資料請求', '問い合わせ', 'お問合せ', 'contact', '予約', 'reserve']
    has_cta = False
    for btn in extractor.buttons:
        if any(kw in btn.lower() for kw in cta_keywords):
            has_cta = True
            break
    # Also check link text
    for link in extractor.links:
        if any(kw in link.get('text', '').lower() for kw in cta_keywords):
            has_cta = True
            break

    if not has_cta and not extractor.buttons:
        issues.append({
            'type': 'no_cta',
            'severity': 'critical',
            'message': 'No CTA button found on landing page',
            'quote': '',
        })
    return issues


def check_lp_pricing(extractor):
    """Check for pricing section."""
    issues = []
    full_text = extractor.get_full_text().lower()
    price_keywords = ['料金', '価格', 'プラン', 'price', 'pricing', '円', '\\$', 'plan',
                      '月額', '年額', '無料']
    if not any(kw in full_text for kw in price_keywords):
        issues.append({
            'type': 'no_pricing',
            'severity': 'warning',
            'message': 'No pricing/plan section detected',
            'quote': '',
        })
    return issues


def check_lp_form(extractor):
    """Check for form presence."""
    issues = []
    if not extractor.has_form:
        issues.append({
            'type': 'no_form',
            'severity': 'warning',
            'message': 'No form element found on landing page',
            'quote': '',
        })
    return issues


def check_lp_size(raw_html):
    """Check page size (simple performance proxy)."""
    issues = []
    size_kb = len(raw_html.encode('utf-8')) / 1024
    if size_kb > 500:
        issues.append({
            'type': 'large_page',
            'severity': 'warning',
            'message': f'Page size is {size_kb:.0f}KB (recommended < 500KB)',
            'quote': '',
        })
    return issues


def check_lp_ogp(extractor):
    """Check OGP tags."""
    issues = []
    required_og = ['og:title', 'og:description', 'og:image']
    for og in required_og:
        if og not in extractor.og_tags:
            issues.append({
                'type': 'missing_ogp',
                'severity': 'warning',
                'message': f'Missing OGP tag: {og}',
                'quote': '',
            })
    return issues


# ============================================================
# Main check orchestrator
# ============================================================

def check_article(html_content, keywords=None):
    """Run all article QA checks. Returns dict with pass/fail and issues list."""
    extractor = HTMLContentExtractor()
    try:
        extractor.feed(html_content)
    except Exception as e:
        return {
            'passed': False,
            'issues': [{'type': 'parse_error', 'severity': 'critical',
                        'message': f'HTML parse error: {e}', 'quote': ''}],
            'summary': {'total': 1, 'critical': 1, 'warning': 0},
        }

    kw_list = keywords if keywords else []
    all_issues = []
    all_issues.extend(check_sns_relevance(extractor, kw_list))
    all_issues.extend(check_text_duplication(extractor))
    all_issues.extend(check_html_errors(extractor, html_content))
    all_issues.extend(check_opening_relevance(extractor, kw_list))
    all_issues.extend(check_images(extractor))
    all_issues.extend(check_links(extractor))
    all_issues.extend(check_meta_tags(extractor))

    critical = sum(1 for i in all_issues if i['severity'] == 'critical')
    warning = sum(1 for i in all_issues if i['severity'] == 'warning')

    return {
        'passed': critical == 0,
        'issues': all_issues,
        'summary': {'total': len(all_issues), 'critical': critical, 'warning': warning},
        'title': extractor.title.strip(),
    }


def check_lp(html_content):
    """Run all LP QA checks."""
    extractor = HTMLContentExtractor()
    try:
        extractor.feed(html_content)
    except Exception as e:
        return {
            'passed': False,
            'issues': [{'type': 'parse_error', 'severity': 'critical',
                        'message': f'HTML parse error: {e}', 'quote': ''}],
            'summary': {'total': 1, 'critical': 1, 'warning': 0},
        }

    all_issues = []
    all_issues.extend(check_lp_responsive(extractor))
    all_issues.extend(check_lp_cta(extractor))
    all_issues.extend(check_lp_pricing(extractor))
    all_issues.extend(check_lp_form(extractor))
    all_issues.extend(check_lp_size(html_content))
    all_issues.extend(check_lp_ogp(extractor))
    all_issues.extend(check_html_errors(extractor, html_content))
    all_issues.extend(check_images(extractor))
    all_issues.extend(check_meta_tags(extractor))

    critical = sum(1 for i in all_issues if i['severity'] == 'critical')
    warning = sum(1 for i in all_issues if i['severity'] == 'warning')

    return {
        'passed': critical == 0,
        'issues': all_issues,
        'summary': {'total': len(all_issues), 'critical': critical, 'warning': warning},
        'title': extractor.title.strip(),
    }


def generate_report_md(result, filepath, keywords=None, check_type='article'):
    """Generate a markdown QA report."""
    name = os.path.basename(filepath)
    verdict = 'PASS' if result['passed'] else 'FAIL'
    lines = [
        f"# QA Report: {name}",
        f"",
        f"**Verdict: {verdict}**",
        f"**Type:** {check_type}",
        f"**Title:** {result.get('title', 'N/A')}",
    ]
    if keywords:
        lines.append(f"**Keywords:** {', '.join(keywords)}")
    lines.append(f"**Issues:** {result['summary']['total']} "
                 f"(critical: {result['summary']['critical']}, "
                 f"warning: {result['summary']['warning']})")
    lines.append("")

    if result['issues']:
        lines.append("## Issues Found")
        lines.append("")
        for i, issue in enumerate(result['issues'], 1):
            sev = 'CRITICAL' if issue['severity'] == 'critical' else 'WARNING'
            lines.append(f"### {i}. [{sev}] {issue['type']}")
            lines.append(f"")
            lines.append(f"{issue['message']}")
            if issue.get('quote'):
                lines.append(f"")
                lines.append(f"> {issue['quote']}")
            lines.append("")
    else:
        lines.append("## No Issues Found")
        lines.append("")

    return '\n'.join(lines)


# ============================================================
# CLI entry point
# ============================================================

def main():
    # Fix Windows encoding
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description='OSCAR2 QA Checker')
    parser.add_argument('file', help='HTML file to check')
    parser.add_argument('--keyword', '-k', help='Target keywords (space-separated)')
    parser.add_argument('--type', '-t', default='article', choices=['article', 'lp'],
                        help='Check type: article or lp')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON')
    parser.add_argument('--report', '-r', help='Save markdown report to file')
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"Error: File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    with open(args.file, 'r', encoding='utf-8') as f:
        html = f.read()

    keywords = args.keyword.split() if args.keyword else []

    if args.type == 'lp':
        result = check_lp(html)
    else:
        result = check_article(html, keywords)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        report = generate_report_md(result, args.file, keywords, args.type)
        print(report)

        if args.report:
            with open(args.report, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"\nReport saved to: {args.report}")

    sys.exit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
