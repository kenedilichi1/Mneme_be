import tempfile
import zipfile
from pathlib import Path

import pytest

from app.modules.documents.services.docling_parser import (
    DoclingParserService,
    UnsupportedDocumentTypeError,
)

# Minimal valid PDF content
SAMPLE_PDF_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 54 >>
stream
BT
/F1 18 Tf
72 700 Td
(Docling PDF Parser Unit Test) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000244 00000 n 
0000000315 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
418
%%EOF
"""


def build_sample_pdf(page_texts: list[str]) -> bytes:
    """A minimal N-page PDF: one Helvetica line per page."""
    page_ids = [4 + 2 * i for i in range(len(page_texts))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, page_texts):
        content = b"BT /F1 12 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        objects.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    startxref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{startxref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def create_sample_epub(file_path: Path) -> None:
    with zipfile.ZipFile(file_path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "   <rootfiles>"
            '      <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "   </rootfiles>"
            "</container>",
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="dcidid" version="2.0">'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "     <dc:title>Test EPUB Book</dc:title>"
            "     <dc:creator>Jane Doe</dc:creator>"
            "  </metadata>"
            "  <manifest>"
            '     <item id="html1" href="chapter1.html" media-type="application/xhtml+xml"/>'
            '     <item id="html2" href="chapter2.html" media-type="application/xhtml+xml"/>'
            "  </manifest>"
            "  <spine>"
            '     <itemref idref="html1"/>'
            '     <itemref idref="html2"/>'
            "  </spine>"
            "</package>",
        )
        zf.writestr(
            "OEBPS/chapter1.html",
            "<html><body><h1>Chapter 1: Overview</h1><p>Docling parses EPUB files properly.</p></body></html>",
        )
        zf.writestr(
            "OEBPS/chapter2.html",
            "<html><body><h1>Chapter 2: Deep Dive</h1><h2>2.1 Details</h2><p>Structured chunking works seamlessly.</p></body></html>",
        )


@pytest.fixture
def parser():
    return DoclingParserService()


def test_parse_pdf_document(parser):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(SAMPLE_PDF_BYTES)
        pdf_path = Path(tmp.name)

    try:
        parsed = parser.parse_document(pdf_path, mime_type="application/pdf")
        assert parsed.file_type == "application/pdf"
        assert parsed.page_count == 1
        assert len(parsed.chunks) > 0
        assert "Docling PDF Parser Unit Test" in parsed.text_content
        first_chunk = parsed.chunks[0]
        assert first_chunk.chunk_index == 0
        assert "Docling PDF Parser Unit Test" in first_chunk.content
        assert first_chunk.page_number == 1
        assert first_chunk.token_count > 0
        # Gapless index: 0..n-1 with no holes
        assert [c.chunk_index for c in parsed.chunks] == list(range(len(parsed.chunks)))
    finally:
        if pdf_path.exists():
            pdf_path.unlink()


def test_parse_epub_document(parser):
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        epub_path = Path(tmp.name)

    try:
        create_sample_epub(epub_path)
        parsed = parser.parse_document(epub_path, mime_type="application/epub+zip")
        assert parsed.file_type == "application/epub+zip"
        assert len(parsed.chunks) >= 2
        assert "Docling parses EPUB files properly" in parsed.text_content

        chunk_1 = parsed.chunks[0]
        assert "Chapter 1: Overview" in (chunk_1.section_title or "") or "Overview" in (chunk_1.section_title or "")
        assert chunk_1.token_count > 0

        chunk_2 = parsed.chunks[1]
        assert "Chapter 2: Deep Dive" in (chunk_2.section_title or "")

        # Gapless, exact Nomic token counts, within the configured budget
        assert [c.chunk_index for c in parsed.chunks] == list(range(len(parsed.chunks)))
        for chunk in parsed.chunks:
            assert chunk.token_count == parser._count_tokens(chunk.content)
            assert chunk.token_count <= parser.max_chunk_tokens
    finally:
        if epub_path.exists():
            epub_path.unlink()


def test_unsupported_document_type(parser):
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(b"plain text file")
        txt_path = Path(tmp.name)

    try:
        with pytest.raises(UnsupportedDocumentTypeError):
            parser.parse_document(txt_path, mime_type="text/plain")
    finally:
        if txt_path.exists():
            txt_path.unlink()


def test_file_not_found(parser):
    missing_path = Path("/nonexistent/file.pdf")
    with pytest.raises(FileNotFoundError):
        parser.parse_document(missing_path)


def test_resolve_input_format(parser):
    from docling.datamodel.base_models import InputFormat

    assert parser.resolve_input_format("test.pdf") == InputFormat.PDF
    assert parser.resolve_input_format("test.epub") == InputFormat.EPUB
    assert parser.resolve_input_format("doc.xyz", mime_type="application/pdf") == InputFormat.PDF
    assert parser.resolve_input_format("doc.xyz", mime_type="application/epub+zip") == InputFormat.EPUB


class WhitespaceTokenizer:
    """Deterministic stand-in for the Nomic tokenizer: words are tokens."""

    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def decode(self, ids: list[str]) -> str:
        return " ".join(ids)


def test_split_enforces_token_budget_and_preserves_text():
    parser = DoclingParserService(max_chunk_tokens=10, tokenizer=WhitespaceTokenizer())
    words = [f"w{i}" for i in range(95)]

    pieces = parser._split_to_token_budget(" ".join(words))

    assert len(pieces) == 10  # ceil(95 / 10)
    assert all(len(p.split()) <= 10 for p in pieces)
    # Order-preserving: reassembly matches the original word sequence
    assert " ".join(pieces).split() == words


def test_split_leaves_text_within_budget_untouched():
    parser = DoclingParserService(max_chunk_tokens=10, tokenizer=WhitespaceTokenizer())
    text = "just a few words"
    assert parser._split_to_token_budget(text) == [text]


def test_epub_chunks_respect_tiny_token_budget():
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        epub_path = Path(tmp.name)

    try:
        create_sample_epub(epub_path)
        parser = DoclingParserService(max_chunk_tokens=8)
        parsed = parser.parse_document(epub_path, mime_type="application/epub+zip")

        assert len(parsed.chunks) > 0
        for chunk in parsed.chunks:
            assert parser._count_tokens(chunk.content) <= 8
            assert chunk.token_count <= 8
            assert chunk.content.strip()
        assert [c.chunk_index for c in parsed.chunks] == list(range(len(parsed.chunks)))
    finally:
        if epub_path.exists():
            epub_path.unlink()


THREE_PAGE_TEXTS = [
    "Chapter one alpha content for page one of the sample.",
    "Chapter two beta content for page two of the sample.",
    "Chapter three gamma content for page three of the sample.",
]


def test_convert_in_batches_keeps_global_page_numbers(tmp_path):
    from docling.datamodel.base_models import InputFormat

    pdf_path = tmp_path / "three_pages.pdf"
    pdf_path.write_bytes(build_sample_pdf(THREE_PAGE_TEXTS))
    parser = DoclingParserService(page_batch_size=1)

    documents = parser._convert_in_batches(pdf_path, InputFormat.PDF)

    assert len(documents) == 3
    for expected_page, doc in enumerate(documents, start=1):
        page_numbers = [
            parser._extract_page_number(chunk)
            for chunk in parser._chunker.chunk(doc)
        ]
        assert page_numbers, f"page {expected_page} produced no chunks"
        # page_range keeps global numbers; a batch must never restart at 1
        assert any(p == expected_page for p in page_numbers), page_numbers
        assert all(p in (expected_page, None) for p in page_numbers), page_numbers


def test_parse_pdf_across_page_batches(tmp_path):
    pdf_path = tmp_path / "three_pages.pdf"
    pdf_path.write_bytes(build_sample_pdf(THREE_PAGE_TEXTS))
    parser = DoclingParserService(page_batch_size=1)

    parsed = parser.parse_document(pdf_path, mime_type="application/pdf")

    assert parsed.page_count == 3
    assert parsed.file_type == "application/pdf"
    for text in ("alpha content", "beta content", "gamma content"):
        assert text in parsed.text_content
    # Gapless index across the chunks produced by every batch
    assert [c.chunk_index for c in parsed.chunks] == list(range(len(parsed.chunks)))
    assert all(1 <= c.page_number <= 3 for c in parsed.chunks if c.page_number)
