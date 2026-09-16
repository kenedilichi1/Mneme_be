from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.modules.auth.dependencies.auth_dependency import CurrentUserDep

documents_router = APIRouter()


@documents_router.post("/uploadfile/")
async def create_upload_file(
    current_user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
):
    if not file:
        return {"message": "No file sent"}
    return {"file": file.filename, "user_id": str(current_user.id)}


@documents_router.get("")
async def list_documents(current_user: CurrentUserDep):
    return {"message": "List documents endpoint", "user_id": str(current_user.id)}


@documents_router.get("/{id}")
async def get_document(id: str, current_user: CurrentUserDep):
    return {"message": f"Get document {id} endpoint", "user_id": str(current_user.id)}


@documents_router.delete("/{id}")
async def delete_document(id: str, current_user: CurrentUserDep):
    return {"message": f"Delete document {id} endpoint", "user_id": str(current_user.id)}


@documents_router.post("/{id}/query")
async def query_document(id: str, current_user: CurrentUserDep):
    return {"message": f"Query document {id} endpoint", "user_id": str(current_user.id)}


@documents_router.get("/{id}/conversations")
async def get_document_conversations(id: str, current_user: CurrentUserDep):
    return {
        "message": f"Get document {id} conversations endpoint",
        "user_id": str(current_user.id),
    }
