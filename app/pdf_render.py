"""Shared Markdown → PDF renderer (ReportLab).

Used by both the multi-agent workflow's `pdf_export` tool (app/workflow.py)
and the single-agent chat's inline PDF artifact endpoint
(app/routers/chat/artifacts.py), so there is exactly one implementation of
"turn structured markdown into a publication-grade PDF" instead of two
copies drifting apart.
"""
import os
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, Preformatted, Image as RLImage,
)


def clean_unicode_text(text: str) -> str:
    """Replace typographic/unicode punctuation ReportLab's default fonts can't
    render, with plain ASCII equivalents."""
    replacements = {
        "‐": "-",  # Hyphen
        "‑": "-",  # Non-breaking hyphen
        "‒": "-",  # Figure dash
        "–": "-",  # En dash (–)
        "—": "--", # Em dash (—)
        "―": "--", # Horizontal bar
        "−": "-",  # Minus sign (−)
        "\xa0": " ",    # Non-breaking space
        "‘": "'",  # Left single quote
        "’": "'",  # Right single quote
        "“": '"',  # Left double quote
        "”": '"',  # Right double quote
        "•": "*",  # Bullet
        "■": "-",       # Fallback replacement
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def _md_inline(t):
    """Convert inline markdown (bold, italic, links, code) to ReportLab markup robustly."""
    t = t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    t = t.replace('&amp;amp;', '&amp;').replace('&amp;lt;', '&lt;').replace('&amp;gt;', '&gt;')

    code_blocks = []
    def _sub_code(m):
        code_blocks.append(m.group(1))
        return f"XYZCODEBLOCK{len(code_blocks)-1}XYZ"

    t = re.sub(r'`(.+?)`', _sub_code, t)

    t = re.sub(r'(?<!\!)\[([^\]]+)\]\([^\)]+\)', r'<font color="#505081"><u>\1</u></font>', t)
    t = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', t)
    t = re.sub(r'___(.+?)___', r'<b><i>\1</i></b>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'__(.+?)__', r'<b>\1</b>', t)
    t = re.sub(r'\*(.+?)\*', r'<i>\1</i>', t)
    t = re.sub(r'_(.+?)_', r'<i>\1</i>', t)

    for idx, cv in enumerate(code_blocks):
        t = t.replace(f"XYZCODEBLOCK{idx}XYZ", f'<font face="Courier" size="8" color="#505081">{cv}</font>')

    t = t.replace('■', '-')
    t = _repair_xml_nesting(t)
    return t


def _repair_xml_nesting(text):
    """Fix incorrectly nested XML/HTML tags that crash ReportLab's strict parser."""
    tag_pattern = re.compile(r'<(/?)(\w+)(?:\s[^>]*)?>')
    stack = []
    result_parts = []
    pos = 0
    for m in tag_pattern.finditer(text):
        result_parts.append(text[pos:m.start()])
        pos = m.end()
        is_close = m.group(1) == '/'
        tag_name = m.group(2).lower()

        if tag_name not in ('b', 'i', 'u', 'font'):
            result_parts.append(m.group(0))
            continue

        if not is_close:
            stack.append(tag_name)
            result_parts.append(m.group(0))
        else:
            if tag_name in stack:
                to_reopen = []
                while stack and stack[-1] != tag_name:
                    popped = stack.pop()
                    result_parts.append(f'</{popped}>')
                    to_reopen.append(popped)
                if stack:
                    stack.pop()
                result_parts.append(f'</{tag_name}>')
                for reopened in reversed(to_reopen):
                    stack.append(reopened)
                    result_parts.append(f'<{reopened}>')
            # else: ignore orphaned closing tag
    result_parts.append(text[pos:])
    return ''.join(result_parts)


def render_markdown_to_pdf(markdown_content: str, dest, image_base_dir: str | None = None) -> None:
    """Render `markdown_content` into a PDF written to `dest`.

    `dest` may be a filesystem path (str) or a file-like object (e.g.
    io.BytesIO) — ReportLab's SimpleDocTemplate accepts either.
    `image_base_dir` resolves relative `![alt](path)` image references; pass
    None when there is no meaningful base directory (e.g. an ephemeral
    in-memory chat artifact with only remote image URLs).
    """
    markdown_content = clean_unicode_text(markdown_content)

    doc = SimpleDocTemplate(dest, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()

    s_title = ParagraphStyle('S_Title', parent=styles['Heading1'], fontSize=22, leading=28,
                              textColor=colors.HexColor('#272757'), spaceAfter=14, keepWithNext=True)
    s_h2 = ParagraphStyle('S_H2', parent=styles['Heading2'], fontSize=16, leading=20,
                           textColor=colors.HexColor('#505081'), spaceBefore=14, spaceAfter=8, keepWithNext=True)
    s_h3 = ParagraphStyle('S_H3', parent=styles['Heading3'], fontSize=13, leading=17,
                           textColor=colors.HexColor('#505081'), spaceBefore=10, spaceAfter=6, keepWithNext=True)
    s_h4 = ParagraphStyle('S_H4', parent=styles['Heading4'], fontSize=11, leading=15,
                           textColor=colors.HexColor('#8686AC'), spaceBefore=8, spaceAfter=4, keepWithNext=True)
    s_body = ParagraphStyle('S_Body', parent=styles['Normal'], fontSize=10, leading=15,
                             textColor=colors.HexColor('#1F2937'), spaceAfter=6)
    s_bullet = ParagraphStyle('S_Bullet', parent=s_body, leftIndent=18, bulletIndent=6)
    s_quote = ParagraphStyle('S_Quote', parent=s_body, leftIndent=20, textColor=colors.HexColor('#6B7280'),
                              borderPadding=4, fontName='Helvetica-Oblique')
    s_tbl_header = ParagraphStyle('S_TblH', parent=styles['Normal'], fontSize=9, leading=12,
                                   textColor=colors.HexColor('#FFFFFF'), fontName='Helvetica-Bold')
    s_tbl_cell = ParagraphStyle('S_TblC', parent=styles['Normal'], fontSize=9, leading=12,
                                 textColor=colors.HexColor('#374151'))

    def _safe_paragraph(text, style):
        try:
            return Paragraph(text, style)
        except Exception:
            clean = re.sub(r'<[^>]+>', '', text)
            try:
                return Paragraph(clean, style)
            except Exception:
                return Paragraph("(Content could not be rendered)", style)

    story = []

    lines = markdown_content.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not stripped:
            i += 1
            continue

        if stripped in ('---', '***', '___'):
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#D1D5DB'),
                                     spaceBefore=8, spaceAfter=8))
            i += 1
            continue

        if '|' in stripped and stripped.startswith('|'):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i].strip())
                i += 1
            rows = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip('|').split('|')]
                if all(set(c.strip()) <= set('-| :') for c in cells):
                    continue
                rows.append(cells)
            if rows:
                col_count = max(len(r) for r in rows)
                tbl_data = []
                for ri, row in enumerate(rows):
                    while len(row) < col_count:
                        row.append('')
                    style = s_tbl_header if ri == 0 else s_tbl_cell
                    tbl_data.append([_safe_paragraph(_md_inline(c), style) for c in row])
                col_w = (letter[0] - 80) / col_count
                t = Table(tbl_data, colWidths=[col_w] * col_count, repeatRows=1)
                t.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#505081')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#F9FAFB'), colors.white]),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ]))
                story.append(Spacer(1, 6))
                story.append(t)
                story.append(Spacer(1, 6))
            # `i` already advanced past every consumed table line, but `stripped`
            # still holds the table's first raw line — without this `continue` it
            # falls through every check below and gets re-appended as a stray
            # plain paragraph (e.g. "| Symptom | Likely Cause | Fix |" duplicated
            # under the rendered table).
            continue

        if stripped in ('<!-- pagebreak -->', '\\pagebreak', r'\pagebreak', '[pagebreak]'):
            story.append(PageBreak())
            i += 1
            continue

        img_match = re.match(r'^!\[(.*?)\]\((.*?)\)$', stripped)
        if img_match:
            img_url = img_match.group(2).strip()
            try:
                import requests
                import tempfile
                import uuid as _uuid

                local_img = img_url
                if img_url.startswith("http"):
                    img_resp = requests.get(img_url, timeout=15)
                    if img_resp.status_code == 200:
                        tmp_path = os.path.join(tempfile.gettempdir(), f"img_{_uuid.uuid4().hex}.png")
                        with open(tmp_path, "wb") as f:
                            f.write(img_resp.content)
                        local_img = tmp_path

                if not local_img.startswith("http"):
                    if not os.path.isabs(local_img) and image_base_dir:
                        local_img = os.path.join(image_base_dir, local_img)

                    if image_base_dir and not os.path.exists(local_img):
                        base_name = os.path.basename(local_img).replace("_", "").lower()
                        for f in os.listdir(image_base_dir):
                            if f.replace("_", "").lower() == base_name:
                                local_img = os.path.join(image_base_dir, f)
                                break

                if os.path.exists(local_img):
                    img = RLImage(local_img)
                    max_w = letter[0] - 80
                    if img.drawWidth > max_w:
                        ratio = max_w / float(img.drawWidth)
                        img.drawWidth = max_w
                        img.drawHeight = img.drawHeight * ratio
                    story.append(Spacer(1, 10))
                    story.append(img)
                    story.append(Spacer(1, 10))
                else:
                    story.append(Spacer(1, 10))
                    story.append(Paragraph(f"<i>[Image missing or unresolvable: {img_url}]</i>", s_quote))
                    story.append(Spacer(1, 10))

                i += 1
                continue
            except Exception as e:
                print(f"Failed to embed image {img_url}: {e}")
                story.append(Paragraph(f"<i>[Error embedding image: {img_url}]</i>", s_quote))
                i += 1
                continue

        if stripped.startswith('```'):
            code_lang = stripped[3:].strip().lower()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1

            code_text = "\n".join(code_lines)

            if code_lang == 'mermaid':
                try:
                    import urllib.parse
                    import requests
                    import tempfile
                    import uuid as _uuid

                    encoded = urllib.parse.quote(code_text)
                    url = f"https://quickchart.io/mermaid?graph={encoded}"
                    img_resp = requests.get(url, timeout=15)

                    if img_resp.status_code == 200:
                        tmp_path = os.path.join(tempfile.gettempdir(), f"mermaid_{_uuid.uuid4().hex}.png")
                        with open(tmp_path, "wb") as f:
                            f.write(img_resp.content)
                        img = RLImage(tmp_path)
                        max_w = letter[0] - 80
                        if img.drawWidth > max_w:
                            ratio = max_w / float(img.drawWidth)
                            img.drawWidth = max_w
                            img.drawHeight = img.drawHeight * ratio
                        story.append(Spacer(1, 10))
                        story.append(img)
                        story.append(Spacer(1, 10))
                        continue
                except Exception as e:
                    print(f"Mermaid rendering failed: {e}")

            code_text = code_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            s_code_block = ParagraphStyle('S_CodeBlock', parent=styles['Normal'],
                                           fontName='Courier', fontSize=8, leading=11,
                                           textColor=colors.HexColor('#0F0E47'))

            p_code = Preformatted(code_text, s_code_block)
            t_code = Table([[p_code]], colWidths=[letter[0] - 80])
            t_code.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F4F7')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#8686AC')),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ]))
            story.append(Spacer(1, 4))
            story.append(t_code)
            story.append(Spacer(1, 4))
            continue

        if stripped.startswith('#### '):
            story.append(_safe_paragraph(_md_inline(stripped[5:]), s_h4))
        elif stripped.startswith('### '):
            story.append(_safe_paragraph(_md_inline(stripped[4:]), s_h3))
        elif stripped.startswith('## '):
            story.append(_safe_paragraph(_md_inline(stripped[3:]), s_h2))
        elif stripped.startswith('# '):
            story.append(_safe_paragraph(_md_inline(stripped[2:]), s_title))
        elif stripped.startswith('> '):
            story.append(_safe_paragraph(_md_inline(stripped[2:]), s_quote))
        elif stripped.startswith('- ') or stripped.startswith('* '):
            story.append(_safe_paragraph("• " + _md_inline(stripped[2:]), s_bullet))
        elif re.match(r'^\d+\.\s', stripped):
            m = re.match(r'^(\d+\.)\s(.*)', stripped)
            story.append(_safe_paragraph(f"{m.group(1)} {_md_inline(m.group(2))}", s_bullet))
        else:
            story.append(_safe_paragraph(_md_inline(stripped), s_body))

        i += 1

    doc.build(story)
