from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.documents.router import router as documents_router
from app.modules.library.router import router as library_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(documents_router, prefix="/documents", tags=["documents"])
api_router.include_router(library_router, prefix="/library", tags=["library"])
