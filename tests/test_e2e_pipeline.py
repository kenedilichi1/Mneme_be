"""E2E pipeline test (MNE-27): upload → confirm → worker → search.

The full HTTP flow runs against the savepoint test session; the worker runs
in-process. Storage I/O and the embedding model are replaced with
deterministic fakes (real Backblaze/network traffic is never touched), while
Docling parsing, chunking, persistence, and all three search modes run for
real.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue.events import DocumentEvent, DocumentEventType
from app.core.queue.queue import rabbitmq
from app.main import app
from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.documents.models.document_model import Document
from app.modules.rag.dependencies import get_embedding_service
from app.modules.user.models.user_model import User
from app.worker import document_worker
from app.worker.document_worker import process_document
from tests.test_docling_parser import SAMPLE_PDF_BYTES

EMAIL = "e2e-pipeline@example.com"
EXPECTED_TEXT = "Docling PDF Parser Unit Test"


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * 768
    vector[index] = 1.0
    return vector


class FakeEmbeddingService:
    """Keyword-routed vectors so 'docling' queries match 'docling' chunks."""

    model = "fake-embedding-model"

    async def generate_embeddings(
        self, texts: list[str], batch_size: int = 100, is_query: bool = False
    ) -> list[list[float]]:
        return [
            _unit_vector(0) if "docling" in text.lower() else _unit_vector(1)
            for text in texts
        ]


class _SharedSession:
    """Yields the test's savepoint session without closing it."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc) -> bool:
        return False


async def _login(client: AsyncClient, db_session: AsyncSession) -> str:
    res = await client.post("/api/v1/auth/request-otp", json={"email": EMAIL})
    assert res.status_code == 202

    user = (
        await db_session.execute(select(User).where(User.email == EMAIL))
    ).scalar_one()
    otp_repo = OtpCodeRepository(db_session)
    await otp_repo.delete_otp_code(user.id)
    await otp_repo.create(
        OtpCode(
            user_id=user.id,
            code_hash=hash_otp_code("654321"),
            expires_at=otp_expiry(),
        )
    )

    res = await client.post(
        "/api/v1/auth/verify-otp", json={"email": EMAIL, "otp": "654321"}
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_upload_confirm_worker_search_pipeline(
    client: AsyncClient, db_session: AsyncSession
):
    token = await _login(client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Upload: create the document row and get a presigned upload URL
    res = await client.post(
        "/api/v1/documents",
        json={
            "title": "Sample",
            "original_filename": "sample.pdf",
            "file_size": len(SAMPLE_PDF_BYTES),
            "file_type": "application/pdf",
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    document_id = res.json()["document"]["id"]
    assert res.json()["upload_url"]

    # 2. Confirm: storage reports the real size and the event is captured
    #    instead of hitting the broker
    captured_events: list[dict] = []

    async def capture_publish(queue_name: str, message: dict) -> None:
        captured_events.append(message)

    with patch.object(
        document_worker.storage_service,
        "get_file_size",
        lambda key: len(SAMPLE_PDF_BYTES),
    ), patch.object(rabbitmq, "publish", capture_publish):
        res = await client.patch(
            f"/api/v1/documents/{document_id}/confirm", headers=headers
        )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "uploaded"
    assert len(captured_events) == 1

    event = DocumentEvent(
        event_type=DocumentEventType(captured_events[0]["event_type"]),
        document_id=captured_events[0]["document_id"],
        user_id=captured_events[0]["user_id"],
        storage_key=captured_events[0]["storage_key"],
    )

    # 3. Worker: parse (real Docling), chunk (Nomic token budget), embed
    #    (fake vectors) and persist — all inside the test transaction
    def fake_download(storage_key: str, path: str) -> None:
        Path(path).write_bytes(SAMPLE_PDF_BYTES)

    with patch.object(
        document_worker.storage_service, "download_file_to_path", fake_download
    ), patch.object(
        document_worker, "embedding_service", FakeEmbeddingService()
    ), patch.object(
        document_worker, "AsyncSessionLocal", lambda: _SharedSession(db_session)
    ):
        await process_document(event)

    res = await client.get(f"/api/v1/documents/{document_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "processed"

    # 4. Search: every mode finds the processed content
    app.dependency_overrides[get_embedding_service] = FakeEmbeddingService
    try:
        for mode in ("text", "vector", "hybrid"):
            res = await client.get(
                "/api/v1/search",
                params={"query": EXPECTED_TEXT, "mode": mode, "limit": 5},
                headers=headers,
            )
            assert res.status_code == 200, res.text
            results = res.json()
            assert results, f"mode {mode!r} returned no results"
            assert any(EXPECTED_TEXT in r["content"] for r in results), (
                f"mode {mode!r} did not find the document content"
            )
            assert all(
                r["document_id"] == document_id for r in results
            ), f"mode {mode!r} leaked another document"
    finally:
        app.dependency_overrides.pop(get_embedding_service, None)
