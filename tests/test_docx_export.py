"""Regression tests for markdown → DOCX fidelity.

Both exporters used to write markdown tables out as literal paragraphs — the
pipes and the |---| divider ended up as body text — and the canvas exporter
stripped **bold**/*italic*/`code` markers without ever applying real
formatting, so emphasis vanished from the document entirely.

Both are easy to reintroduce: a table row that falls through to a generic
"else: add_paragraph(text)" branch still *looks* fine in a diff, and inline
emphasis silently disappears rather than erroring. These assert on the actual
saved document rather than on the code shape.
"""
import io
import os
import unittest

os.environ.setdefault("GROQ_API_KEY", "mock-key")
os.environ.setdefault("GOOGLE_API_KEY", "mock-key")

from docx import Document

from app.docx_md import is_table_divider, is_table_row, parse_table

MD = """# Report

Text with **bold**, *italic* and `code()`.

| Symptom | Cause |
|---------|-------|
| Slow **boot** | Disk I/O |
| Crash | Race |

- bullet with **bold**
"""


def _formatted_runs(doc):
    """(flags, text) for every run carrying real character formatting."""
    out = []
    for p in doc.paragraphs:
        for r in p.runs:
            flags = "".join(f for f, on in (
                ("B", r.bold), ("I", r.italic), ("C", r.font.name == "Consolas"),
            ) if on)
            if flags:
                out.append((flags, r.text))
    return out


class TestTableParsing(unittest.TestCase):
    def test_row_and_divider_detection(self):
        self.assertTrue(is_table_row("| a | b |"))
        self.assertFalse(is_table_row("just text"))
        # A bulleted line mentioning a pipe must not be mistaken for a table.
        self.assertFalse(is_table_row("- use a | b"))
        self.assertTrue(is_table_divider("|---|:---:|"))
        self.assertFalse(is_table_divider("| a | b |"))

    def test_divider_dropped_and_ragged_rows_padded(self):
        lines = ["| a | b | c |", "|---|---|---|", "| 1 |", "after"]
        rows, nxt = parse_table(lines, 0)
        self.assertEqual(nxt, 3, "should stop at the first non-table line")
        self.assertEqual(rows[0], ["a", "b", "c"])
        # Short row padded, so writing it can't IndexError.
        self.assertEqual(rows[1], ["1", "", ""])


class TestWorkflowDocxExport(unittest.TestCase):
    def test_table_becomes_a_real_table_and_emphasis_survives(self):
        from app.workflow import docx_export

        res = docx_export.invoke({"markdown_content": MD, "filename": "kokomi_test_export.docx"})
        self.assertIn("at: ", res, f"export failed: {res}")
        path = res.split("at: ")[-1].strip()
        try:
            doc = Document(path)

            self.assertEqual(len(doc.tables), 1, "markdown table did not become a Word table")
            table = doc.tables[0]
            self.assertEqual(len(table.rows), 3, "expected header + 2 body rows (divider dropped)")
            self.assertEqual([c.text for c in table.rows[0].cells], ["Symptom", "Cause"])

            # The pipe syntax must not also appear as body text.
            body = "\n".join(p.text for p in doc.paragraphs)
            self.assertNotIn("|---", body)
            self.assertNotIn("| Symptom", body)

            texts = [t for _, t in _formatted_runs(doc)]
            self.assertIn("bold", texts)
            self.assertIn("italic", texts)
            self.assertIn("code()", texts)
            # Markers themselves must be gone, not merely unstyled.
            self.assertNotIn("**", body)
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestCanvasDocxExport(unittest.TestCase):
    def test_emphasis_is_applied_not_just_stripped(self):
        from app.routers.canvas import _to_blocks, _build_docx

        doc = Document(io.BytesIO(_build_docx(_to_blocks(MD, "document"), "Report")))

        self.assertEqual(len(doc.tables), 1, "markdown table did not become a Word table")
        runs = _formatted_runs(doc)
        self.assertTrue(runs, "no run carried any formatting — emphasis was stripped, not applied")
        texts = [t for _, t in runs]
        self.assertIn("bold", texts)
        self.assertIn("italic", texts)
        self.assertIn("code()", texts)

        body = "\n".join(p.text for p in doc.paragraphs)
        self.assertNotIn("|---", body)
        self.assertNotIn("**", body)

    def test_table_cells_keep_their_own_emphasis(self):
        from app.routers.canvas import _to_blocks, _build_docx

        doc = Document(io.BytesIO(_build_docx(_to_blocks(MD, "document"), "Report")))
        cell = doc.tables[0].rows[1].cells[0]          # "Slow **boot**"
        self.assertEqual(cell.text, "Slow boot")
        self.assertTrue(
            any(r.bold and r.text == "boot" for r in cell.paragraphs[0].runs),
            "emphasis inside a table cell was not applied",
        )

    def test_other_block_writers_still_accept_table_blocks(self):
        """The PDF and markdown writers share these blocks, and a table block
        carries a list of rows rather than a string — passing it to a string
        API would raise."""
        from app.routers.canvas import _to_blocks, _build_pdf, _blocks_to_markdown

        blocks = _to_blocks(MD, "document")
        self.assertIn("table", [k for k, _, _ in blocks])

        pdf = _build_pdf(blocks, "Report")
        self.assertTrue(pdf.startswith(b"%PDF-"))

        md = _blocks_to_markdown(blocks)
        self.assertIn("| Symptom | Cause |", md)
        self.assertIn("| --- | --- |", md, "round-trip lost the header divider")


if __name__ == "__main__":
    unittest.main()
