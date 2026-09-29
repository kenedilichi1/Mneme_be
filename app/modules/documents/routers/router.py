import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.responses import COMMON_ERRORS, SuccessEnvelope, error_responses, ok
from app.modules.auth.dependencies.auth_dependency import CurrentUserDep
from app.modules.documents.dependencies.document_dependency import DocumentServiceDep
from app.modules.documents.schemas.document_schema import (
    DocumentCreate,
    DocumentPublic,
    DocumentWithUploadUrl,
)

documents_router = APIRouter()

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@documents_router.post(
    "",
    response_model=SuccessEnvelope[DocumentWithUploadUrl],
    status_code=201,
    responses=COMMON_ERRORS
    | error_responses(
        (401, "Not authenticated"),
        (413, "Declared file size exceeds the upload limit"),
    ),
)
async def create_document(
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
    payload: DocumentCreate,
):
    document, upload_url = await document_service.create_document(
        user_id=current_user.id, payload=payload
    )
    return ok(
        {
            "document": document,
            "upload_url": upload_url,
            "upload_headers": {"Content-Type": document.file_type},
        },
        "Document created",
    )


@documents_router.get(
    "",
    response_model=SuccessEnvelope[list[DocumentPublic]],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def list_documents(
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    documents = await document_service.list_documents(
        user_id=current_user.id, limit=limit, offset=offset
    )
    return ok(documents, "Documents retrieved")


@documents_router.get(
    "/{document_id}",
    response_model=SuccessEnvelope[DocumentPublic],
    responses=COMMON_ERRORS
    | error_responses(
        (401, "Not authenticated"),
        (404, "Document not found"),
    ),
)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    document = await document_service.get_document(
        document_id=document_id, user_id=current_user.id
    )
    return ok(document, "Document retrieved")


@documents_router.patch(
    "/{document_id}/confirm",
    response_model=SuccessEnvelope[DocumentPublic],
    responses=COMMON_ERRORS
    | error_responses(
        (401, "Not authenticated"),
        (400, "Document cannot be confirmed in its current state, or the file was never uploaded"),
        (404, "Document not found"),
        (413, "Uploaded file exceeds the upload limit"),
        (503, "Upload confirmed but processing could not be queued; retry confirm"),
    ),
)
async def confirm_upload(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    document = await document_service.confirm_upload(
        document_id=document_id, user_id=current_user.id
    )
    return ok(document, "Upload confirmed")


@documents_router.delete(
    "/{document_id}",
    status_code=204,
    responses=COMMON_ERRORS
    | error_responses(
        (401, "Not authenticated"),
        (404, "Document not found"),
    ),
)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    # 204 stays bodyless: HTTP gives it no body, so it is the one documented
    # exception to the envelope (see app/core/responses.py).
    await document_service.delete_document(
        document_id=document_id, user_id=current_user.id
    )
