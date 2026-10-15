import logging
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict
from docling.chunking import HybridChunker
from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import DoclingDocument
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption, EpubFormatOption

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def _load_nomic_tokenizer(model_name: str, revision: str):
    """The tokenizer of the embedding model, loaded once per process.

    Token counts and the chunk budget must use the *same* tokenizer as the
    embedding model (pinned to the same commit as EMBEDDING_MODEL_REVISION).
    """
    from transformers import AutoTokenizer

    logger.info("Loading Nomic tokenizer: %s @ %s", model_name, revision)
    return AutoTokenizer.from_pretrained(model_name, revision=revision)


class ParsedChunk(BaseModel):
    chunk_index: int
    content: str
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    token_count: int

    model_config = ConfigDict(from_attributes=True)


class ParsedDocument(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    page_count: Optional[int] = None
    file_type: str
    text_content: str
    chunks: List[ParsedChunk] = []

    model_config = ConfigDict(from_attributes=True)


class UnsupportedDocumentTypeError(ValueError):
    """Raised when an unsupported document format is provided to DoclingParserService."""
    pass


class DoclingParserService:
    """
    Document parsing service powered by IBM Docling.
    Supports PDF ('application/pdf') and EPUB ('application/epub+zip') documents.

    Chunks are bounded by ``max_chunk_tokens`` in Nomic (embedding-model)
    tokens, ``token_count`` is the exact tokenizer count of the chunk text,
    and ``chunk_index`` is gapless (0..n-1) over every emitted chunk.

    PDFs are converted in page batches (``page_batch_size``) so peak memory
    stays bounded: one pass over a multi-hundred-page PDF exhausts the
    worker's memory and the process is OOM-killed, which the broker surfaces
    as a redelivered message and burns a processing attempt.
    """

    MIME_TYPE_MAP: dict[str, InputFormat] = {
        "application/pdf": InputFormat.PDF,
        "application/epub+zip": InputFormat.EPUB,
    }

    EXTENSION_MAP: dict[str, InputFormat] = {
        ".pdf": InputFormat.PDF,
        ".epub": InputFormat.EPUB,
    }

    def __init__(
        self,
        max_chunk_tokens: Optional[int] = None,
        tokenizer=None,
        page_batch_size: Optional[int] = None,
    ) -> None:
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.EPUB],
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self._build_pdf_pipeline_options()
                )
            },
        )
        self._chunker = HybridChunker()
        self.max_chunk_tokens = max_chunk_tokens or settings.MAX_CHUNK_TOKENS
        self.page_batch_size = page_batch_size or settings.DOCLING_PAGE_BATCH_SIZE
        # Injectable for tests; lazily loaded from the pinned model otherwise.
        self._tokenizer = tokenizer

    @staticmethod
    def _build_pdf_pipeline_options() -> PdfPipelineOptions:
        options = PdfPipelineOptions()
        # Every extra inference thread holds its own activations; on a
        # memory-capped worker that is what tips a large PDF into the OOM
        # killer. Two threads keep throughput acceptable within the budget.
        options.accelerator_options = AcceleratorOptions(
            num_threads=settings.DOCLING_NUM_THREADS
        )
        return options

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            self._tokenizer = _load_nomic_tokenizer(
                settings.EMBEDDING_MODEL, settings.EMBEDDING_MODEL_REVISION
            )
        return self._tokenizer

    def resolve_input_format(
        self, file_path: Path | str, mime_type: Optional[str] = None
    ) -> InputFormat:
        path = Path(file_path)

        if mime_type and mime_type.lower() in self.MIME_TYPE_MAP:
            return self.MIME_TYPE_MAP[mime_type.lower()]

        ext = path.suffix.lower()
        if ext in self.EXTENSION_MAP:
            return self.EXTENSION_MAP[ext]

        raise UnsupportedDocumentTypeError(
            f"Unsupported file type for parsing: filename='{path.name}', mime_type='{mime_type}'. "
            f"Docling parsing layer currently supports PDF and EPUB files."
        )

    def _convert_in_batches(
        self, path: Path, input_format: InputFormat
    ) -> List[DoclingDocument]:
        """Convert a PDF ``page_batch_size`` pages at a time, in order.

        A single pass over a multi-hundred-page PDF keeps every rendered page
        and the growing document tree alive at once; on a memory-capped worker
        the kernel OOM-kills it and the broker redelivers the job, which the
        worker counts as a failed attempt. Each batch is a separate
        ``convert`` call, so the previous batch's document is freed before the
        next one starts.

        ``page_range`` keeps *global* page numbers in the provenance of the
        converted items, so chunk ``page_number`` stays correct across batches.
        EPUB has no page concept and goes through a single pass.
        """
        if input_format != InputFormat.PDF:
            return [self._converter.convert(path).document]

        documents: List[DoclingDocument] = []
        page_count: Optional[int] = None
        start = 1
        while page_count is None or start <= page_count:
            end = start + self.page_batch_size - 1
            result = self._converter.convert(path, page_range=(start, end))
            page_count = result.input.page_count
            if not result.document.pages:
                break
            logger.info(
                "Converted pages %d-%d of %s (%d pages total)",
                start,
                min(end, page_count),
                path.name,
                page_count,
            )
            documents.append(result.document)
            start = end + 1

        if not documents:
            raise ValueError(f"Docling produced no pages for document: {path.name}")
        return documents

    def parse_document(
        self, file_path: Path | str, mime_type: Optional[str] = None
    ) -> ParsedDocument:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Document file not found: {path}")

        input_format = self.resolve_input_format(path, mime_type)
        logger.info("Parsing %s document with Docling: %s", input_format.value, path)

        documents = self._convert_in_batches(path, input_format)

        text_content = "\n\n".join(
            doc.export_to_markdown() for doc in documents
        ).strip()

        page_count = sum(len(doc.pages) for doc in documents) or None

        title = None
        author = None
        if hasattr(documents[0], "name") and documents[0].name:
            title = documents[0].name

        chunks: List[ParsedChunk] = []
        for doc in documents:
            for doc_chunk in self._chunker.chunk(doc):
                content = doc_chunk.text.strip()
                if not content:
                    continue

                page_number = self._extract_page_number(doc_chunk)
                section_title = self._extract_section_title(doc_chunk)

                # Enforce the token budget and keep the index gapless: splitting
                # or skipping must not leave holes in chunk_index.
                for piece in self._split_to_token_budget(content):
                    chunks.append(
                        ParsedChunk(
                            chunk_index=len(chunks),
                            content=piece,
                            page_number=page_number,
                            section_title=section_title,
                            token_count=self._count_tokens(piece),
                        )
                    )

        inferred_mime_type = (
            mime_type
            if mime_type
            else (
                "application/pdf"
                if input_format == InputFormat.PDF
                else "application/epub+zip"
            )
        )

        return ParsedDocument(
            title=title,
            author=author,
            page_count=page_count,
            file_type=inferred_mime_type,
            text_content=text_content,
            chunks=chunks,
        )

    def _extract_page_number(self, doc_chunk) -> Optional[int]:
        if not hasattr(doc_chunk, "meta") or not doc_chunk.meta:
            return None

        if hasattr(doc_chunk.meta, "doc_items") and doc_chunk.meta.doc_items:
            for item in doc_chunk.meta.doc_items:
                if hasattr(item, "prov") and item.prov:
                    for prov_item in item.prov:
                        if hasattr(prov_item, "page_no") and prov_item.page_no:
                            return int(prov_item.page_no)
        return None

    def _extract_section_title(self, doc_chunk) -> Optional[str]:
        if not hasattr(doc_chunk, "meta") or not doc_chunk.meta:
            return None

        if hasattr(doc_chunk.meta, "headings") and doc_chunk.meta.headings:
            return " > ".join(doc_chunk.meta.headings)
        return None

    def _count_tokens(self, text: str) -> int:
        """Exact token count in the embedding model's tokenizer (no special tokens)."""
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def _split_to_token_budget(self, content: str) -> list[str]:
        """Split text so every piece is <= max_chunk_tokens Nomic tokens."""
        out: list[str] = []
        self._split_into(content, self.max_chunk_tokens, out)
        return out

    def _split_into(self, piece: str, window: int, out: list[str]) -> None:
        """Depth-first, order-preserving split at `window`-token id windows.

        A decoded slice is re-encoded to verify it fits the budget; on a
        tokenizer round-trip mismatch the window halves for the next round
        (strictly smaller, so recursion terminates). window == 1 is the
        base case: single-token pieces, nothing further to split.
        """
        piece = piece.strip()
        if not piece:
            return
        ids = self.tokenizer.encode(piece, add_special_tokens=False)
        if len(ids) <= self.max_chunk_tokens or window <= 1:
            out.append(piece)
            return

        step = min(window, len(ids))
        slices = [
            self.tokenizer.decode(ids[start : start + step]).strip()
            for start in range(0, len(ids), step)
        ]
        budget_ok = all(
            len(self.tokenizer.encode(s, add_special_tokens=False))
            <= self.max_chunk_tokens
            for s in slices
            if s
        )
        child_window = step if budget_ok else max(1, step // 2)
        for s in slices:
            self._split_into(s, child_window, out)
