from fastapi import APIRouter

from app.core.responses import COMMON_ERRORS, SuccessEnvelope, error_responses, ok
from app.modules.auth.dependencies.auth_dependency import CurrentUserDep

router = APIRouter()


@router.post(
    "/query",
    response_model=SuccessEnvelope[dict[str, str]],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def query_library(current_user: CurrentUserDep):
    return ok({"user_id": str(current_user.id)}, "Query library endpoint")


@router.get(
    "/conversations",
    response_model=SuccessEnvelope[dict[str, str]],
    responses=COMMON_ERRORS | error_responses((401, "Not authenticated")),
)
async def get_library_conversations(current_user: CurrentUserDep):
    return ok({"user_id": str(current_user.id)}, "Get library conversations endpoint")
