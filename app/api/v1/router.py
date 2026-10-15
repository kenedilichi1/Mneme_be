from fastapi import APIRouter

from app.modules.auth.routers.auth_router import auth_router
from app.modules.chat.routers.chat_router import chat_router
from app.modules.documents.routers.router import documents_router
from app.modules.library.router import router as library_router
from app.modules.rag.routers.search_router import search_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(documents_router, prefix="/documents", tags=["documents"])
# Document Q&A: shares the /documents prefix ({document_id}/query, …/conversations)
api_router.include_router(chat_router, prefix="/documents", tags=["chat"])
api_router.include_router(library_router, prefix="/library", tags=["library"])
api_router.include_router(search_router, prefix="/search", tags=["search"])
