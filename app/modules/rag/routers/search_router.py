import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Query

from app.core.responses import COMMON_ERRORS, SuccessEnvelope, error_responses, ok
from app.modules.auth.dependencies.auth_dependency import CurrentUserDep
from app.modules.rag.dependencies import VectorSearchServiceDep
from app.modules.rag.services.vector_search_service import ChunkSearchResult, SearchMode

search_router = APIRouter()


@search_router.get(
    "",
    response_model=SuccessEnvelope[list[ChunkSearchResult]],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def search(
    current_user: CurrentUserDep,
    search_service: VectorSearchServiceDep,
    query: Annotated[str, Query(min_length=1, max_length=1000)],
    document_id: Annotated[Optional[uuid.UUID], Query()] = None,
    mode: Annotated[SearchMode, Query()] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
):
    """Search the current user's chunks by vector, full-text, or hybrid (RRF).

    Results are always scoped to the authenticated user; `document_id`
    optionally narrows the search to a single owned document.
    """
    results = await search_service.search(
        query=query,
        user_id=current_user.id,
        document_id=document_id,
        limit=limit,
        mode=mode,
    )
    return ok(results, "Search completed")
