from sqlalchemy.ext.asyncio import AsyncSession


from app.modules.auth.models.otp_code_model import OtpCode
from app.modules.auth.repositories.otp_code_repository import OtpCodeRepository
from app.modules.auth.services.secrets import hash_otp_code, otp_expiry
from app.modules.documents.models.document_chunk_model import DocumentChunk
from app.modules.documents.models.document_model import Document
from app.modules.documents.repositories.document_repository import DocumentRepository
from app.modules.user.models.user_model import User
from app.modules.user.repositories.user_repository import UserRepository


async def test_user_repository_crud(db_session: AsyncSession):

    user_repo = UserRepository(db_session)
    user = User(
        email="testuser@example.com",
        first_name="Test",
        last_name="User",
    )
    created = await user_repo.create(user)
    assert created.id is not None
    assert created.email == "testuser@example.com"

    fetched_by_id = await user_repo.get_by_id(created.id)
    assert fetched_by_id is not None
    assert fetched_by_id.email == "testuser@example.com"

    fetched_by_email = await user_repo.get_by_email("testuser@example.com")
    assert fetched_by_email is not None
    assert fetched_by_email.id == created.id


async def test_otp_code_repository_lifecycle(db_session: AsyncSession):
    user_repo = UserRepository(db_session)
    otp_repo = OtpCodeRepository(db_session)

    user = await user_repo.create(User(email="otpuser@example.com"))

    otp = OtpCode(
        user_id=user.id,
        code_hash=hash_otp_code("123456"),
        expires_at=otp_expiry(),
    )
    created_otp = await otp_repo.create(otp)
    assert created_otp.id is not None
    assert created_otp.user_id == user.id

    fetched_otp = await otp_repo.get_otp_code(user.id)
    assert fetched_otp is not None
    assert fetched_otp.id == created_otp.id

    await otp_repo.delete_otp_code(user.id)
    assert await otp_repo.get_otp_code(user.id) is None


async def test_document_and_chunks_with_vector(db_session: AsyncSession):
    user_repo = UserRepository(db_session)
    doc_repo = DocumentRepository(db_session)

    user = await user_repo.create(User(email="docuser@example.com"))

    doc = Document(
        user_id=user.id,
        title="PostgreSQL 18 Manual",
        author="PostgreSQL Global Development Group",
        original_filename="pg18.pdf",
        file_size=50000,
        page_count=100,
        storage_key=f"{user.id}/manual.pdf",
        file_type="application/pdf",
        status="pending_upload",
    )
    created_doc = await doc_repo.create(doc)
    assert created_doc.id is not None

    # Test pgvector embedding storage
    embedding_sample = [0.01] * 768
    chunk = DocumentChunk(
        document_id=created_doc.id,
        user_id=user.id,
        chunk_index=0,
        page_number=1,
        content="Introduction to pgvector in PostgreSQL 18",
        token_count=8,
        embedding_model="text-embedding-3-small",
        embedding=embedding_sample,
    )
    db_session.add(chunk)
    await db_session.flush()
    await db_session.refresh(chunk)

    assert chunk.id is not None
    assert len(chunk.embedding) == 768

    # Fetch user documents
    user_docs = await doc_repo.get_all_by_user(user.id)
    assert len(user_docs) == 1
    assert user_docs[0].id == created_doc.id
