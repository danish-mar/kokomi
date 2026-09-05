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
    HRFlowable, PageBreak, Preformatted, Image as RLImage, KeepTogether,
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

    # ── Image layout ────────────────────────────────────────────────────
    # Images used to get one treatment regardless of shape: scale to fit,
    # drop it in, caption underneath. That wastes most of the page on a tall
    # portrait and makes a square photo float in a sea of white. Instead each
    # image is classified by its real aspect ratio (read off the decoded file,
    # never assumed) and laid out accordingly.
    CONTENT_W = letter[0] - 80          # page width minus both margins
    MAX_IMG_H = letter[1] - 120         # leave room for margins/running space
    s_caption = ParagraphStyle('S_Caption', parent=s_body, fontSize=9, leading=13,
                               textColor=colors.HexColor('#6B7280'),
                               fontName='Helvetica-Oblique', spaceBefore=4)

    def _classify(iw, ih):
        ratio = float(iw) / float(ih)
        if ratio >= 1.4:
            return "wide"
        if ratio >= 0.85:
            return "square"
        return "tall"

    def _fit(img, box_w, box_h):
        """Scale in place to fit a box, never enlarging."""
        scale = min(box_w / float(img.drawWidth), box_h / float(img.drawHeight), 1.0)
        img.drawWidth *= scale
        img.drawHeight *= scale
        return img

    def _side_by_side(img, caption, image_left):
        """Image in one column, caption text in the other."""
        text = _safe_paragraph(_md_inline(caption), s_body)
        gutter = 12
        text_w = CONTENT_W - img.drawWidth - gutter
        row = [img, text] if image_left else [text, img]
        widths = ([img.drawWidth + gutter, text_w] if image_left
                  else [text_w, img.drawWidth + gutter])
        t = Table([row], colWidths=widths)
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return t

    # Portrait images alternate sides so a run of them doesn't stack the page
    # lopsidedly down one edge.
    tall_image_left = True

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
                    # Decode it now rather than letting ReportLab do it during
                    # doc.build(): a download that isn't really an image (an
                    # HTML error page, an unsupported format) constructs an
                    # Image flowable happily and only fails at build time —
                    # outside this try/except, taking the whole document with
                    # it instead of degrading to a placeholder.
                    from reportlab.lib.utils import ImageReader
                    reader = ImageReader(local_img)
                    iw, ih = reader.getSize()
                    if not iw or not ih:
                        raise ValueError("image has no dimensions")

                    img = RLImage(local_img)
                    caption = img_match.group(1).strip()
                    kind = _classify(iw, ih)

                    # Every branch fits BOTH dimensions. Clamping only the width
                    # lets a portrait image scale to the page width and end up
                    # taller than the page, which ReportLab refuses to lay out
                    # ("too large on page") and which fails the entire render.
                    if kind == "wide" or not caption:
                        # Full bleed across the text column, caption underneath.
                        _fit(img, CONTENT_W, MAX_IMG_H)
                        img.hAlign = 'CENTER'
                        block = [Spacer(1, 10), img]
                        if caption:
                            block.append(_safe_paragraph(_md_inline(caption), s_caption))
                        block.append(Spacer(1, 10))
                    elif kind == "square":
                        # Portrait-card shape: image left at ~40% of the column,
                        # the caption reading down the right.
                        _fit(img, CONTENT_W * 0.40, MAX_IMG_H * 0.5)
                        block = [Spacer(1, 10), _side_by_side(img, caption, True), Spacer(1, 10)]
                    else:
                        # Tall: a narrower column still leaves usable room for
                        # text beside it, and alternating sides keeps a run of
                        # portraits from hugging one edge.
                        _fit(img, CONTENT_W * 0.32, MAX_IMG_H * 0.62)
                        block = [Spacer(1, 10),
                                 _side_by_side(img, caption, tall_image_left),
                                 Spacer(1, 10)]
                        tall_image_left = not tall_image_left

                    # Keep the image with its caption — a page break between the
                    # two orphans the caption at the top of the next page.
                    story.append(KeepTogether(block))
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

    try:
        doc.build(story)
    except Exception as build_err:
        # Layout happens only here, so a flowable that can't be placed (an
        # oversized image, an unbreakable line) fails the whole render after
        # every element has already been accepted. Rather than returning
        # nothing for an otherwise fine document, rebuild without the images —
        # they're the usual culprit and the least essential part.
        print(f"[pdf_render] build failed ({build_err}); retrying without images")
        if hasattr(dest, "seek"):
            dest.seek(0)
            dest.truncate()
        # Images are now wrapped in KeepTogether (and, for side-by-side
        # layouts, a Table), so an isinstance check on the top-level flowable
        # alone would keep every one of them and fail the retry the same way.
        def _has_image(flowable):
            if isinstance(flowable, RLImage):
                return True
            for attr in ("_content", "_cellvalues"):
                nested = getattr(flowable, attr, None)
                if not nested:
                    continue
                for item in nested:
                    items = item if isinstance(item, (list, tuple)) else [item]
                    if any(_has_image(x) for x in items):
                        return True
            return False

        fallback = [f for f in story if not _has_image(f)]
        placeholder = Paragraph(
            "<i>[Some images could not be rendered and were omitted.]</i>", s_quote
        )
        doc = SimpleDocTemplate(dest, pagesize=letter, rightMargin=40, leftMargin=40,
                                topMargin=40, bottomMargin=40)
        doc.build(fallback + [Spacer(1, 10), placeholder])
