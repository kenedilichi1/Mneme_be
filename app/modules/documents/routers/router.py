import uuid

from fastapi import APIRouter

from app.modules.auth.dependencies.auth_dependency import CurrentUserDep
from app.modules.documents.dependencies.document_dependency import DocumentServiceDep
from app.modules.documents.schemas.document_schema import (
    DocumentConfirmUpload,
    DocumentCreate,
    DocumentPublic,
    DocumentWithUploadUrl,
)

documents_router = APIRouter()


@documents_router.post("", response_model=DocumentWithUploadUrl, status_code=201)
async def create_document(
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
    payload: DocumentCreate,
):
    document, upload_url = await document_service.create_document(
        user_id=current_user.id, payload=payload
    )
    return {"document": document, "upload_url": upload_url}


@documents_router.get("", response_model=list[DocumentPublic])
async def list_documents(
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    return await document_service.list_documents(user_id=current_user.id)


@documents_router.get("/{document_id}", response_model=DocumentPublic)
async def get_document(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    return await document_service.get_document(
        document_id=document_id, user_id=current_user.id
    )


@documents_router.patch("/{document_id}/confirm", response_model=DocumentPublic)
async def confirm_upload(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    return await document_service.confirm_upload(
        document_id=document_id, user_id=current_user.id
    )


@documents_router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    current_user: CurrentUserDep,
    document_service: DocumentServiceDep,
):
    await document_service.delete_document(
        document_id=document_id, user_id=current_user.id
    )
