#!/usr/bin/env python3
"""
HF Course to PDF Converter — Playwright-based v2
Converts Hugging Face MDX course repos to clean, structured PDFs.

Usage:
    python3 convert_playwright.py <course_key> [--output-dir ~/HF-PDFs]

Course keys: smol-course, agents-course, deep-rl-course, audio-course,
             computer-vision-course, diffusion-course, hf-course,
             ml-for-3d-course, ml-games-course
"""

import os
import re
import sys
import yaml
import asyncio
import argparse
import json
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# Course registry
# ---------------------------------------------------------------------------
COURSES = {
    "smol-course": {
        "title": "Smol Course",
        "subtitle": "Fine-Tuning Language Models — from beginner to expert",
        "content_dir": "units/en",
        "github": "huggingface/smol-course",
    },
    "agents-course": {
        "title": "AI Agents Course",
        "subtitle": "Building AI Agents with Hugging Face",
        "content_dir": "units/en",
        "github": "huggingface/agents-course",
    },
    "deep-rl-course": {
        "title": "Deep Reinforcement Learning Course",
        "subtitle": "Learn Deep RL from scratch",
        "content_dir": "units/en",
        "github": "huggingface/deep-rl-class",
    },
    "audio-course": {
        "title": "Audio Course",
        "subtitle": "Audio Transformers Course",
        "content_dir": "chapters/en",
        "github": "huggingface/audio-transformers-course",
    },
    "computer-vision-course": {
        "title": "Computer Vision Course",
        "subtitle": "Learn Computer Vision with Transformers",
        "content_dir": "chapters/en",
        "github": "huggingface/computer-vision-course",
    },
    "diffusion-course": {
        "title": "Diffusion Models Course",
        "subtitle": "Learn Diffusion Models from scratch",
        "content_dir": None,
        "github": "huggingface/diffusion-models-class",
    },
    "hf-course": {
        "title": "LLM Course",
        "subtitle": "Natural Language Processing with Transformers",
        "content_dir": "chapters/en",
        "github": "huggingface/course",
    },
    "ml-for-3d-course": {
        "title": "ML for 3D Course",
        "subtitle": "Machine Learning for 3D Data",
        "content_dir": "units/en",
        "github": "huggingface/ml-for-3d-course",
    },
    "ml-games-course": {
        "title": "ML for Games Course",
        "subtitle": "Making Games with AI",
        "content_dir": "units/en",
        "github": "huggingface/making-games-with-ai-course",
    },
}

BASE_DIR = "/home/ubuntu/HF-Courses"
DEFAULT_OUTPUT = "/home/ubuntu/HF-PDFs"

# ---------------------------------------------------------------------------
# MDX → Clean Markdown preprocessor
# ---------------------------------------------------------------------------

CALLOUT_MAP = {
    "TIP":       ("💡 Tip", "#0ea5e9"),
    "NOTE":      ("📝 Note", "#6366f1"),
    "WARNING":   ("⚠️ Warning", "#f59e0b"),
    "CAUTION":   ("🚨 Caution", "#ef4444"),
    "IMPORTANT": ("❗ Important", "#8b5cf6"),
}

def convert_callouts(text: str) -> str:
    """
    Convert GitHub-style callouts to styled divs.
    > [!TIP] ... → <div class="callout callout-tip"><div class="callout-title">💡 Tip</div><div class="callout-body">...</div></div>
    Also converts markdown links [text](url) and **bold** inside the body to HTML.
    """
    lines = text.split('\n')
    out = []
    i = 0
    while i < len(lines):
        m = re.match(r'^>\s*\[!(TIP|NOTE|WARNING|CAUTION|IMPORTANT)\]\s*$', lines[i], re.IGNORECASE)
        if m:
            ctype = m.group(1).upper()
            title, color = CALLOUT_MAP.get(ctype, (ctype, "#666"))
            # Collect all subsequent lines that start with '>'
            body_lines = []
            i += 1
            while i < len(lines) and lines[i].startswith('>'):
                line = lines[i][1:].lstrip()
                if line == '' and i + 1 < len(lines) and not lines[i+1].startswith('>'):
                    break
                body_lines.append(line)
                i += 1
            body = '\n'.join(body_lines).strip()
            # Convert markdown inline formatting to HTML inside the callout body.
            # Order matters: code blocks first (to avoid $$ or ** inside code), then images, links, bold, code.
            # Handle fenced code blocks (```...```) inside callout bodies
            def replace_fenced(m):
                lang = m.group(1) or ''
                code = m.group(2)
                # Escape HTML entities in the code
                code = code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                if lang:
                    return f'<pre><code class="language-{lang}">{code}</code></pre>'
                return f'<pre><code>{code}</code></pre>'
            body = re.sub(r'```(\w*)\n(.*?)```', replace_fenced, body, flags=re.DOTALL)
            body = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" />', body)
            body = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', body)
            body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)
            body = re.sub(r'`([^`]+)`', r'<code>\1</code>', body)
            out.append(f'<div class="callout callout-{ctype.lower()}">')
            out.append(f'  <div class="callout-title" style="color:{color}">{title}</div>')
            out.append(f'  <div class="callout-body">{body}</div>')
            out.append('</div>')
            continue
        out.append(lines[i])
        i += 1
    return '\n'.join(out)


def strip_wikilinks(text: str) -> str:
    text = text.replace("[[", "").replace("]]", "")
    return text


def convert_youtube_iframes(text: str) -> str:
    def replace_iframe(match):
        full = match.group(0)
        src_match = re.search(r'src="([^"]+)"', full)
        if not src_match:
            return ""
        src = src_match.group(1)
        vid = None
        m2 = re.search(r'(?:youtube\.com/embed/|youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]+)', src)
        if m2:
            vid = m2.group(1)
        if vid:
            return f'\n\n▶ [Watch on YouTube](https://www.youtube.com/watch?v={vid})\n'
        return f'\n\n🔗 [Interactive Demo]({src})\n'

    text = re.sub(r'<iframe[^>]*>.*?</iframe>', replace_iframe, text, flags=re.DOTALL)
    text = re.sub(r'<iframe[^>]*/?>', replace_iframe, text, flags=re.DOTALL)
    return text


def convert_youtube_components(text: str) -> str:
    def replace_yt(match):
        yt_id = match.group(1)
        return f'▶ [Watch on YouTube](https://www.youtube.com/watch?v={yt_id})'
    text = re.sub(r'<Youtube\s+id="([^"]+)"\s*/>', replace_yt, text)
    text = re.sub(r'<Youtube\s+id="([^"]+)"/>', replace_yt, text)
    return text


def strip_mdx_imports(text: str) -> str:
    lines = text.split('\n')
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith('import ') or s.startswith('export '):
            continue
        out.append(line)
    return '\n'.join(out)


def strip_jsx_components(text: str) -> str:
    text = re.sub(r'<[A-Z][a-zA-Z0-9]*\s+[^>]*/>', '', text)
    text = re.sub(r'<[A-Z][a-zA-Z0-9]*\s+[^>]*>.*?</[A-Z][a-zA-Z0-9]*>', '', text, flags=re.DOTALL)
    text = re.sub(r'\{#if[^}]*\}', '', text)
    text = re.sub(r'\{:else\}', '', text)
    text = re.sub(r'\{/if\}', '', text)
    # Removed \{[^{}]+\} — too aggressive, strips LaTeX args like \frac{1}{2}
    return text


def convert_html_images(text: str) -> str:
    def replace_figure(match):
        fig = match.group(0)
        img_match = re.search(r'<img[^>]+src="([^"]+)"[^>]*/?>', fig)
        alt_match = re.search(r'<img[^>]+alt="([^"]*)"', fig)
        caption_match = re.search(r'<figcaption>(.*?)</figcaption>', fig, re.DOTALL)
        if img_match:
            src = img_match.group(1)
            alt = alt_match.group(1) if alt_match else ""
            caption = caption_match.group(1).strip() if caption_match else ""
            if caption:
                return f'\n<figure>\n<img src="{src}" alt="{alt}" /><figcaption>{caption}</figcaption>\n</figure>\n'
            return f'\n<img src="{src}" alt="{alt}" />\n'
        return ''
    text = re.sub(r'<figure[^>]*>.*?</figure>', replace_figure, text, flags=re.DOTALL)
    # Keep standalone <img> tags as HTML (don't convert to markdown ![]())
    # This ensures they work inside callouts (HTML blocks) without markdown parser issues.
    return text


def markdown_images_to_html(text: str) -> str:
    """Convert markdown ![alt](url) to <img> tags before callout conversion,
    so they don't get swallowed by HTML block parsing."""
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" />', text)
    return text


def convert_latex(text: str) -> str:
    text = re.sub(r'\\\[', '$$', text)
    text = re.sub(r'\\\]', '$$', text)
    text = re.sub(r'\\\(', '$', text)
    text = re.sub(r'\\\)', '$', text)
    return text


# Placeholder prefix for LaTeX blocks — using HTML comments so the markdown
# parser preserves them without encoding or stripping.
PH_DISPLAY = '<!--MATHBLOCK_DISP_'
PH_INLINE = '<!--MATHBLOCK_INLINE_'
PH_END = '-->'


def protect_latex(text: str) -> tuple:
    """
    Replace $...$ and $$...$$ blocks with placeholders so the markdown
    parser doesn't mangle them (e.g. interpreting _ as emphasis).
    Returns (protected_text, list_of_math_blocks).
    """
    math_blocks = []

    # Extract display math first ($$...$$)
    def extract_display(m):
        idx = len(math_blocks)
        math_blocks.append(m.group(0))
        return f'{PH_DISPLAY}{idx}{PH_END}'

    text = re.sub(r'\$\$(.+?)\$\$', extract_display, text, flags=re.DOTALL)

    # Extract inline math ($...$) — be careful not to match $$
    def extract_inline(m):
        idx = len(math_blocks)
        math_blocks.append(m.group(0))
        return f'{PH_INLINE}{idx}{PH_END}'

    text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', extract_inline, text)

    return text, math_blocks


def restore_latex(text: str, math_blocks: list) -> str:
    """Restore math blocks from placeholders."""
    for i, block in enumerate(math_blocks):
        text = text.replace(f'{PH_DISPLAY}{i}{PH_END}', f'\n\n{block}\n\n')
        text = text.replace(f'{PH_INLINE}{i}{PH_END}', block)
    return text


def render_katex_batch(blocks: list) -> list:
    """Render all LaTeX blocks via Node.js KaTeX in one shot.
    Each block is like '$$...$$' or '$...$'.
    Returns list of HTML strings (full katex HTML) in same order.
    """
    import subprocess, json

    items = []
    for i, block in enumerate(blocks):
        is_display = block.startswith('$$')
        # Strip $, spaces, and newlines — KaTeX doesn't like leading/trailing whitespace
        latex = block.strip('$ \n\r\t')
        items.append({"index": i, "latex": latex, "display_mode": is_display})

    js_path = os.path.join(os.path.dirname(__file__), 'render_katex.js')
    if not os.path.exists(js_path):
        # Fallback: return original blocks
        return blocks

    try:
        proc = subprocess.run(
            ['node', js_path],
            input=json.dumps(items),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"  ⚠️ KaTeX render error: {proc.stderr[:200]}")
            return blocks
        results = json.loads(proc.stdout)
        # Sort by index, return HTML
        results.sort(key=lambda r: r['index'])
        return [r['html'] for r in results]
    except Exception as e:
        print(f"  ⚠️ KaTeX render exception: {e}")
        return blocks


def resolve_image_paths(text: str, source_path: str, repo_path: str, github_repo: str) -> str:
    from urllib.parse import quote
    def fix_path(match):
        full = match.group(0)
        # Figure out which group is src and which is alt based on the regex that matched
        # For HTML <img> tags: group(1)=src, group(2)=alt
        # For markdown ![alt](url): group(1)=alt, group(2)=url
        if full.startswith('<img'):
            img_path = match.group(1) or ''
            alt = match.group(2) or ''
        else:
            alt = match.group(1) or ''
            img_path = match.group(2) or ''
        if img_path.startswith('http'):
            return full  # already a full URL, keep as-is
        # Try local paths first
        source_dir = os.path.dirname(source_path)
        abs_path = os.path.normpath(os.path.join(source_dir, img_path))
        if os.path.exists(abs_path):
            return f'<img src="file://{abs_path}" alt="{alt}" />'
        abs_path2 = os.path.normpath(os.path.join(repo_path, img_path))
        if os.path.exists(abs_path2):
            return f'<img src="file://{abs_path2}" alt="{alt}" />'
        # Encode path to be URL-safe (handles spaces, special chars)
        safe_path = quote(img_path.lstrip('./'), safe='/:@!$&\'()*+,;=-._~')
        raw_url = f'https://raw.githubusercontent.com/{github_repo}/main/{safe_path}'
        return f'<img src="{raw_url}" alt="{alt}" />'
    # Handle both markdown images ![alt](url) and HTML <img> tags
    text = re.sub(r'<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)"[^>]*/?>', fix_path, text)
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_path, text)
    return text


def preprocess_mdx(text: str, source_path: str = "", repo_path: str = "", github_repo: str = "") -> str:
    text = strip_mdx_imports(text)
    text = strip_jsx_components(text)
    text = strip_wikilinks(text)
    text = convert_youtube_iframes(text)
    text = convert_youtube_components(text)
    text = convert_html_images(text)
    # Convert ALL markdown images to HTML <img> tags early, before the markdown
    # parser runs. This prevents images from being swallowed by HTML block boundaries
    # (callouts, raw HTML regions, etc.).
    text = markdown_images_to_html(text)
    text = convert_callouts(text)
    if source_path and repo_path and github_repo:
        text = resolve_image_paths(text, source_path, repo_path, github_repo)
    # Convert <details>/<summary> to styled HTML divs instead of stripping.
    # This preserves the collapsible output sections with proper styling.
    def replace_details(match):
        full = match.group(0)
        summary_m = re.search(r'<summary>(.*?)</summary>', full, re.DOTALL | re.IGNORECASE)
        summary_text = summary_m.group(1).strip() if summary_m else 'Output'
        # Extract content between </summary> and </details>
        body = re.sub(r'.*?</summary>', '', full, count=1, flags=re.DOTALL | re.IGNORECASE)
        body = re.sub(r'</details>', '', body, flags=re.DOTALL | re.IGNORECASE).strip()
        # Escape HTML in the body to avoid parser issues, then convert inline markdown
        body = body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'\n<div class="details-summary">▸ {summary_text}</div>\n<div class="details-content"><pre><code>{body}</code></pre></div>\n'
    text = re.sub(r'<details>.*?</details>', replace_details, text, flags=re.DOTALL | re.IGNORECASE)
    # Fallback: strip any remaining details/summary tags
    text = re.sub(r'<details>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</details>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<summary>(.*?)</summary>', r'**\1:**', text, flags=re.DOTALL | re.IGNORECASE)
    # Convert "```python output" blocks to regular "```python" for proper syntax highlighting
    # (output blocks aren't real code, but rendering as code keeps formatting intact)
    text = re.sub(r'```python output', '```text', text)
    # Remove special Unicode spacing characters that cause garbled output
    text = re.sub(r'[\u2000-\u200f\u2028-\u202f\u205f-\u206f]', '', text)
    # Replace multiple consecutive spaces (from terminal output alignment) with a single space
    text = re.sub(r' {3,}', '  ', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def get_content_files(repo_path: str, content_dir):
    # Check for toctree at the repo root first, then inside content_dir
    toctree_path = os.path.join(repo_path, "_toctree.yml")
    if content_dir:
        nested_toctree = os.path.join(repo_path, content_dir, "_toctree.yml")
    else:
        nested_toctree = None

    if os.path.exists(toctree_path):
        return parse_toctree(repo_path, toctree_path, content_dir)
    elif nested_toctree and os.path.exists(nested_toctree):
        return parse_toctree(repo_path, nested_toctree, content_dir)

    if not content_dir:
        return []
    full_dir = os.path.join(repo_path, content_dir)
    if not os.path.exists(full_dir):
        return []
    files = []
    for root, dirs, filenames in os.walk(full_dir):
        dirs[:] = [d for d in dirs if d not in ('events', 'hackathon')]
        for f in sorted(filenames):
            if f.endswith(('.mdx', '.md')) and f != 'README.md':
                files.append(os.path.join(root, f))
    return files


def parse_toctree(repo_path: str, toctree_path: str, content_dir: str = None) -> list:
    with open(toctree_path) as f:
        toc = yaml.safe_load(f)
    files = []
    for section in toc:
        for subsection in section.get('sections', []):
            local = subsection.get('local', '')
            if not local:
                continue
            # Try with content_dir prefix first, then repo root
            candidates = []
            if content_dir:
                candidates.append(os.path.join(repo_path, content_dir, local))
            candidates.append(os.path.join(repo_path, local))
            found = False
            for base in candidates:
                for ext in ('.mdx', '.md'):
                    filepath = base + ext
                    if os.path.exists(filepath):
                        files.append(filepath)
                        found = True
                        break
                if found:
                    break
    return files


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{TITLE}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<style>
    @page {
        size: A4;
        margin: 2cm 2.5cm;
    }
    * { box-sizing: border-box; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
        font-size: 14px; line-height: 1.7; color: #1a1a2e;
        max-width: 100%; margin: 0 auto; padding: 20px;
    }
    .title-page { text-align: center; padding: 100px 40px 60px; page-break-after: always; }
    .title-page h1 { font-size: 36px; color: #1a1a2e; margin-bottom: 10px; }
    .title-page .subtitle { font-size: 18px; color: #555; margin-bottom: 40px; }
    .title-page .meta { font-size: 13px; color: #888; }
    .title-page .source-link { color: #2563eb; text-decoration: none; }
    .toc-page { page-break-after: always; padding: 40px 0; }
    .toc-heading { font-size: 28px; color: #1a1a2e; border-bottom: 2px solid #2563eb; padding-bottom: 12px; margin-bottom: 24px; }
    .toc-list { list-style: none; padding: 0; margin: 0; }
    .toc-list li { padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 15px; }
    .toc-list li a { color: #2563eb; text-decoration: none; }
    .toc-list li a:hover { text-decoration: underline; }
    .chapter { page-break-before: always; }
    .chapter:first-of-type { page-break-before: auto; }
    .chapter-title { font-size: 24px; color: #1a1a2e; border-bottom: 2px solid #2563eb; padding-bottom: 10px; margin-bottom: 20px; }
    h1 { font-size: 22px; color: #1a1a2e; margin-top: 30px; }
    h2 { font-size: 18px; color: #2d2d4e; margin-top: 24px; }
    h3 { font-size: 16px; color: #3d3d5e; margin-top: 20px; }
    h4 { font-size: 14px; color: #4d4d6e; margin-top: 16px; }
    a { color: #2563eb; text-decoration: none; }
    a:hover { text-decoration: underline; }
    code { background: #f0f0f5; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace; }
    pre { background: #1e1e2e; color: #cdd6f4; padding: 16px 20px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.6; margin: 16px 0; page-break-inside: avoid; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    pre code { background: none; padding: 0; color: inherit; font-size: inherit; white-space: pre-wrap; overflow-wrap: anywhere; }
    pre, code { max-width: 100%; }
    .chapter-content { max-width: 100%; overflow: visible; }
    .chapter-content pre { max-width: 100%; }
    pre code { max-width: 100%; }
    .details-summary { font-weight: 700; font-size: 13px; color: #6366f1; margin: 16px 0 0 0; padding: 10px 14px; background: #f5f3ff; border-radius: 6px 6px 0 0; border-left: 3px solid #6366f1; cursor: default; }
    .details-content { margin: 0 0 16px 0; }
    .details-content pre { margin: 0; border-radius: 0 0 6px 6px; background: #1a1a2e; font-size: 11px; line-height: 1.4; max-height: 400px; overflow-y: auto; }
    .details-content pre code { color: #a0a0c0; }
    .callout { border-radius: 8px; padding: 16px 20px; margin: 20px 0; page-break-inside: avoid; border-left: 4px solid; }
    .callout-title { font-weight: 700; font-size: 14px; margin-bottom: 8px; }
    .callout-body { font-size: 13px; line-height: 1.6; }
    .callout-body p { margin: 6px 0; }
    .callout-tip { background: #f0f9ff; border-color: #0ea5e9; }
    .callout-note { background: #f5f3ff; border-color: #6366f1; }
    .callout-warning { background: #fffbeb; border-color: #f59e0b; }
    .callout-caution { background: #fef2f2; border-color: #ef4444; }
    .callout-important { background: #faf5ff; border-color: #8b5cf6; }
    .details-summary { font-weight: 700; font-size: 14px; color: #6366f1; margin: 16px 0 8px 0; padding: 8px 12px; background: #f5f3ff; border-radius: 6px; border-left: 3px solid #6366f1; }
    .details-content { margin: 0 0 16px 0; padding: 8px 12px 12px 16px; border: 1px solid #e0e0e5; border-radius: 0 6px 6px 6px; border-top: none; }
    blockquote { border-left: 4px solid #a78bfa; margin: 16px 0; padding: 12px 20px; background: #faf5ff; border-radius: 0 8px 8px 0; page-break-inside: avoid; }
    blockquote p { margin: 6px 0; font-size: 13px; }
    img { max-width: 100%; height: auto; border-radius: 8px; margin: 16px 0; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; page-break-inside: avoid; }
    th, td { border: 1px solid #ddd; padding: 10px 14px; text-align: left; }
    th { background: #f0f0f5; font-weight: 600; }
    tr { page-break-inside: avoid; }
    hr { border: none; border-top: 1px solid #e0e0e5; margin: 30px 0; }
    .highlight { border-radius: 8px; }
    .katex-container .katex { font-size: 1.1em; }
    .katex-container .katex-display { margin: 16px 0; padding: 12px 16px; background: #faf5ff; border-radius: 8px; overflow-x: auto; page-break-inside: avoid; }
</style>
</head>
<body>
<div class="title-page">
    <h1>{TITLE}</h1>
    <div class="subtitle">{SUBTITLE}</div>
    <div class="meta">By Hugging Face<br><br><a class="source-link" href="https://github.com/{GITHUB_REPO}">github.com/{GITHUB_REPO}</a></div>
</div>
<div class="toc-page">
    <h1 class="toc-heading">Table of Contents</h1>
    <ul class="toc-list">{TOC_ITEMS}</ul>
</div>
{ALL_HTML}
<script>
document.addEventListener('DOMContentLoaded', function() {
    hljs.highlightAll();
    try {
        renderMathInElement(document.body, {
            delimiters: [
                {left: '$$', right: '$$', display: true},
                {left: '$', right: '$', display: false}
            ],
            throwOnError: false,
            trust: true
        });
    } catch(e) {
        console.error('KaTeX auto-render error:', e);
    }
});
</script>
</body>
</html>"""


def embed_images_base64(html: str) -> str:
    """
    Download external images and embed them as base64 data URIs.
    Uses retries and fallback to ensure images appear in the PDF.
    """
    import urllib.request
    import ssl
    import base64

    def _download(match):
        tag = match.group(0)
        src_m = re.search(r'src="([^"]+)"', tag)
        alt_m = re.search(r'alt="([^"]*)"', tag)
        url = src_m.group(1) if src_m else ''
        alt = alt_m.group(1) if alt_m else ''
        if not url or url.startswith('data:'):
            return tag

        # URL-decode the URL first, then re-encode properly
        from urllib.parse import unquote, quote
        url_clean = unquote(url)
        # Split on ? to keep query params intact
        if '?' in url_clean:
            base, qs = url_clean.split('?', 1)
            url_encoded = quote(base, safe='/:@!$&\'()*+,;=-._~') + '?' + qs
        else:
            url_encoded = quote(url_clean, safe='/:@!$&\'()*+,;=-._~')

        last_error = None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url_encoded,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
                        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Connection': 'keep-alive',
                    }
                )
                with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                    data = resp.read()
                if len(data) == 0:
                    raise Exception('empty response')
                break
            except Exception as e:
                last_error = e
                if attempt < 2:
                    import time
                    time.sleep(1 * (attempt + 1))
                data = None

        if data and len(data) > 100:
            ext = url_encoded.rsplit('.', 1)[-1].lower().split('?')[0].split('#')[0]
            mime_map = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                        'gif': 'image/gif', 'webp': 'image/webp', 'svg': 'image/svg+xml'}
            mime = mime_map.get(ext, 'image/png')
            b64 = base64.b64encode(data).decode('ascii')
            return f'<img src="data:{mime};base64,{b64}" alt="{alt}" />'
        else:
            print(f"  ⚠️  Image download failed after 3 retries: {url[:80]} - {last_error}")
            return tag

    html = re.sub(r'<img\s[^>]*src="([^"]+)"[^>]*>', lambda m: _download(m), html)
    return html




def add_pdf_bookmarks(pdf_path: str, chapters: list):
    """
    Post-process PDF with pypdf to add outline/bookmark entries for each chapter.
    This makes chapters navigable in the PDF viewer sidebar.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        print("  ⚠️  pypdf not installed, skipping PDF bookmarks")
        return

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)

    # Add outline items. pypdf uses add_outline_item(page, title, parent=None)
    # We need to estimate which page each chapter starts on.
    # Unfortunately, pypdf can't directly map HTML chapters to PDF pages.
    # But we can use the fact that page numbers are sequential.
    # A simpler approach: add TOC entries that link to the TOC page,
    # and rely on the HTML-generated TOC for actual navigation.
    # OR we can try to find chapter headings in the PDF text.

    # Simple approach: estimate page per chapter based on content length.
    # Each chapter has a page break, so chapter N starts roughly at page N+1
    # (title page + toc page + chapter pages)
    total_chapters = len(chapters)
    total_pages = len(reader.pages)
    if total_chapters == 0:
        return

    # Estimate: title page (1) + toc page (1) + each chapter takes roughly
    # (total_pages - 2) / total_chapters pages
    pages_per_chapter = max(1, (total_pages - 2) / total_chapters) if total_pages > 2 else 1
    root = writer.add_outline_item("Table of Contents", 1)  # page 1 (0-indexed: 1)
    for i, chap in enumerate(chapters):
        title = chap.get('title', f'Chapter {i+1}')
        page_num = int(2 + i * pages_per_chapter)
        if page_num < total_pages:
            writer.add_outline_item(title, page_num, parent=root)

    writer.write(pdf_path)
    print(f"  ✅ Added {len(chapters)} PDF bookmark entries")


def build_html(title: str, subtitle: str, github_repo: str, chapters: list) -> str:
    import markdown as md_lib

    extensions = [
        'fenced_code', 'codehilite', 'tables', 'toc', 'md_in_html',
    ]
    extension_configs = {
        'codehilite': {'css_class': 'highlight', 'linenums': False},
    }
    md = md_lib.Markdown(extensions=extensions, extension_configs=extension_configs)

    # Build TOC
    toc_items = []
    for i, chap in enumerate(chapters):
        chap_title = chap.get('title', f'Chapter {i+1}')
        toc_items.append(f'<li><a href="#chap-{i}">{chap_title}</a></li>')

    # Build chapters — collect all math blocks across chapters
    all_math_blocks = []
    chapters_with_indices = []

    for i, chap in enumerate(chapters):
        chap_md = chap['content']
        chap_title = chap.get('title', f'Chapter {i+1}')
        # Convert \[ \] and \( \) to $$ and $
        chap_md = convert_latex(chap_md)
        # Extract math blocks
        protected, blocks = protect_latex(chap_md)
        offset = len(all_math_blocks)
        all_math_blocks.extend(blocks)
        html_body = md.reset().convert(protected)
        chapters_with_indices.append({
            'title': chap_title,
            'offset': offset,
            'n_blocks': len(blocks),
            'html_body': html_body,
        })

    # Pre-render all LaTeX blocks via Node.js KaTeX
    print(f"  Rendering {len(all_math_blocks)} LaTeX blocks via KaTeX...")
    rendered_blocks = render_katex_batch(all_math_blocks)

    # Replace placeholders with rendered KaTeX HTML
    chapter_html = []
    for i, c in enumerate(chapters_with_indices):
        html = c['html_body']
        for j in range(c['n_blocks']):
            idx = c['offset'] + j
            block = rendered_blocks[idx] if idx < len(rendered_blocks) else ''
            html = html.replace(f'{PH_DISPLAY}{idx}{PH_END}', block)
            html = html.replace(f'{PH_INLINE}{idx}{PH_END}', block)
        chapter_html.append(f'<div class="chapter" id="chap-{i}">')
        chapter_html.append(f'<h1 class="chapter-title">{c["title"]}</h1>')
        chapter_html.append(f'<div class="chapter-content">{html}</div>')
        chapter_html.append('</div>')

    result = HTML_TEMPLATE
    result = result.replace('{TITLE}', title)
    result = result.replace('{SUBTITLE}', subtitle)
    result = result.replace('{GITHUB_REPO}', github_repo)
    result = result.replace('{TOC_ITEMS}', '\n'.join(toc_items))
    result = result.replace('{ALL_HTML}', '\n'.join(chapter_html))
    # Post-process: embed images as base64
    result = embed_images_base64(result)
    return result


# ---------------------------------------------------------------------------
# Playwright PDF generation
# ---------------------------------------------------------------------------

async def html_to_pdf(html_content: str, output_path: str):
    from playwright.async_api import async_playwright

    html_path = output_path.replace('.pdf', '_temp.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-web-security', '--allow-file-access-from-files'])
        page = await browser.new_page()
        await page.goto(f'file://{html_path}', wait_until='load')
        # Give KaTeX CSS and highlight.js time to fully apply
        await page.wait_for_timeout(2000)
        await page.pdf(
            path=output_path,
            format='A4',
            margin={'top': '2cm', 'right': '2.5cm', 'bottom': '2cm', 'left': '2.5cm'},
            print_background=True,
            display_header_footer=False,
        )
        await browser.close()

    os.remove(html_path)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def convert_course(course_key: str, output_dir: str = DEFAULT_OUTPUT):
    config = COURSES.get(course_key)
    if not config:
        print(f"❌ Unknown course: {course_key}")
        print(f"Available: {', '.join(COURSES.keys())}")
        return None

    repo_path = os.path.join(BASE_DIR, course_key)
    if not os.path.exists(repo_path):
        print(f"❌ Repo not found: {repo_path}")
        return None

    print(f"\n{'='*60}")
    print(f"Converting: {course_key}")
    print(f"{'='*60}")

    files = get_content_files(repo_path, config.get('content_dir'))
    if not files:
        print(f"  ⚠️  No content files found, skipping")
        return None
    print(f"  Found {len(files)} content files")

    chapters = []
    for i, filepath in enumerate(files):
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            raw = f.read()

        body = raw
        title = ""
        lines = raw.split('\n')
        if lines and lines[0].strip() == '---':
            end_idx = None
            for j, line in enumerate(lines[1:50], 1):
                if line.strip() == '---':
                    end_idx = j
                    break
            if end_idx:
                try:
                    fm = yaml.safe_load('\n'.join(lines[1:end_idx]))
                    if fm and isinstance(fm, dict):
                        title = fm.get('title', '')
                except:
                    pass
                body = '\n'.join(lines[end_idx+1:])

        clean = preprocess_mdx(
            body,
            source_path=filepath,
            repo_path=repo_path,
            github_repo=config['github']
        )

        if len(clean.strip()) < 30:
            continue

        if not title:
            m = re.search(r'^#\s+(.+)$', clean, re.MULTILINE)
            if m:
                title = m.group(1).strip()

        # Strip the first H1 from content to avoid duplicate title rendering
        # (the chapter title is already rendered by <h1 class="chapter-title">)
        clean = re.sub(r'^#\s+.*\n?', '', clean, count=1)

        chapters.append({'title': title, 'content': clean})

        if (i + 1) % 20 == 0:
            print(f"  Processed {i+1}/{len(files)} files...")

    print(f"  → {len(chapters)} chapters after filtering")

    html = build_html(
        title=config['title'],
        subtitle=config['subtitle'],
        github_repo=config['github'],
        chapters=chapters
    )

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{course_key}.pdf")
    print(f"  Generating PDF...")
    asyncio.run(html_to_pdf(html, output_path))

    # Add PDF bookmarks/outline for chapter navigation
    add_pdf_bookmarks(output_path, chapters)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  ✅ PDF saved: {output_path} ({size_mb:.1f} MB)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description='Convert HF courses to PDF')
    parser.add_argument('course', help='Course key (or "all" for all courses)')
    parser.add_argument('--output-dir', default=DEFAULT_OUTPUT, help='Output directory')
    args = parser.parse_args()

    if args.course == 'all':
        for key in COURSES:
            convert_course(key, args.output_dir)
    else:
        convert_course(args.course, args.output_dir)


if __name__ == '__main__':
    main()
