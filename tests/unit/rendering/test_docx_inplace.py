"""Unit tests for the format-preserving in-place DOCX tailorer.

The point of the test file is to prove **only run.text changes** — no bold /
italic / font size / color / paragraph structure survives a swap. When no
swap applies, the output must be a byte-for-byte copy of the input.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from docx import Document
from docx.shared import RGBColor, Pt

from magicapply.config.models import KeywordEntry
from magicapply.infrastructure.rendering.docx_inplace import InPlaceDocxTailorer


def _make_multi_run_docx(path: Path) -> Path:
    """Build a fixture DOCX with one paragraph containing three runs with
    distinct formatting (bold / italic / plain colored), so a run-level swap
    is provably per-run."""
    doc = Document()
    p = doc.add_paragraph()

    run_bold = p.add_run("Built ")
    run_bold.bold = True

    run_italic = p.add_run("distributed systems")
    run_italic.italic = True

    run_colored = p.add_run(" for cluster observability")
    run_colored.font.color.rgb = RGBColor(0x33, 0x66, 0x99)
    run_colored.font.size = Pt(11)

    doc.save(str(path))
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestNoSwaps:
    def test_empty_bank_produces_byte_identical_copy(self, tmp_path: Path) -> None:
        src = _make_multi_run_docx(tmp_path / "src.docx")
        out = tmp_path / "out.docx"

        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[],
            jd_terms=["microservices"],
            out_path=out,
        )
        assert _sha256(out) == _sha256(src)

    def test_no_jd_term_matches_produces_byte_identical_copy(
        self, tmp_path: Path
    ) -> None:
        src = _make_multi_run_docx(tmp_path / "src.docx")
        out = tmp_path / "out.docx"

        entry = KeywordEntry(
            term="microservices",
            synonyms=["distributed systems"],
            evidence="ok",
        )
        # JD does not mention "microservices" → entry is not applicable → no swap.
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["rust", "webassembly"],
            out_path=out,
        )
        assert _sha256(out) == _sha256(src)

    def test_missing_source_docx_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="source DOCX missing"):
            InPlaceDocxTailorer().render(
                source_docx=tmp_path / "nope.docx",
                matched_bank=[],
                jd_terms=[],
                out_path=tmp_path / "out.docx",
            )


class TestSwapPreservesFormatting:
    def test_swap_replaces_synonym_only_in_matching_run(
        self, tmp_path: Path
    ) -> None:
        src = _make_multi_run_docx(tmp_path / "src.docx")
        out = tmp_path / "out.docx"

        entry = KeywordEntry(
            term="microservices",
            synonyms=["distributed systems", "SOA"],
            evidence="ok",
        )
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["microservices"],
            out_path=out,
        )

        doc = Document(str(out))
        runs = doc.paragraphs[0].runs

        # First run: unchanged ("Built ") — still bold.
        assert runs[0].text == "Built "
        assert runs[0].bold is True

        # Second run: text swapped, italic preserved.
        assert runs[1].text == "microservices"
        assert runs[1].italic is True

        # Third run: unchanged — colored + sized.
        assert runs[2].text == " for cluster observability"
        assert runs[2].font.color.rgb == RGBColor(0x33, 0x66, 0x99)
        assert runs[2].font.size == Pt(11)

    def test_case_insensitive_swap_whole_word(self, tmp_path: Path) -> None:
        # Resume run says "Distributed Systems" (Title Case); JD says
        # "microservices". Swap is case-insensitive; replacement uses the
        # canonical bank term as-is.
        doc = Document()
        doc.add_paragraph().add_run("Led Distributed Systems migration")
        src = tmp_path / "src.docx"
        doc.save(str(src))

        entry = KeywordEntry(
            term="microservices",
            synonyms=["distributed systems"],
            evidence="ok",
        )
        out = tmp_path / "out.docx"
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["microservices"],
            out_path=out,
        )
        text = Document(str(out)).paragraphs[0].runs[0].text
        assert "microservices" in text
        assert "Distributed Systems" not in text

    def test_word_boundary_no_substring_bleed(self, tmp_path: Path) -> None:
        # Bank synonym "sql". Resume run has "PostgreSQL". Whole-word match
        # must not eat the SQL suffix.
        doc = Document()
        doc.add_paragraph().add_run("Deep PostgreSQL experience")
        src = tmp_path / "src.docx"
        doc.save(str(src))

        entry = KeywordEntry(term="postgres", synonyms=["sql"], evidence="ok")
        out = tmp_path / "out.docx"
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["postgres"],
            out_path=out,
        )
        text = Document(str(out)).paragraphs[0].runs[0].text
        assert "PostgreSQL" in text  # untouched

    def test_swap_in_table_cell(self, tmp_path: Path) -> None:
        doc = Document()
        table = doc.add_table(rows=1, cols=1)
        table.rows[0].cells[0].paragraphs[0].add_run("Owns distributed systems observability")
        src = tmp_path / "src.docx"
        doc.save(str(src))

        entry = KeywordEntry(
            term="microservices",
            synonyms=["distributed systems"],
            evidence="ok",
        )
        out = tmp_path / "out.docx"
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["microservices"],
            out_path=out,
        )
        cell_text = (
            Document(str(out))
            .tables[0]
            .rows[0]
            .cells[0]
            .paragraphs[0]
            .runs[0]
            .text
        )
        assert "microservices" in cell_text
        assert "distributed systems" not in cell_text


class TestMultipleEntries:
    def test_longer_synonym_wins_when_overlapping(self, tmp_path: Path) -> None:
        # Two bank entries where one synonym is a prefix of another.
        # Sorted-by-length-desc means the longer synonym fires first.
        doc = Document()
        doc.add_paragraph().add_run(
            "Managed multi-agent orchestration across GPU clusters"
        )
        src = tmp_path / "src.docx"
        doc.save(str(src))

        entry = KeywordEntry(
            term="agentic ai",
            synonyms=["multi-agent orchestration", "multi-agent"],
            evidence="ok",
        )
        out = tmp_path / "out.docx"
        InPlaceDocxTailorer().render(
            source_docx=src,
            matched_bank=[entry],
            jd_terms=["agentic ai"],
            out_path=out,
        )
        text = Document(str(out)).paragraphs[0].runs[0].text
        # "multi-agent orchestration" swapped to "agentic ai" — the shorter
        # "multi-agent" synonym does not double-fire because the longer
        # match consumed its span first.
        assert "agentic ai across GPU clusters" in text
        assert "multi-agent" not in text
