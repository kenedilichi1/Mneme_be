from fastapi import APIRouter

from app.modules.auth.dependencies.auth_dependency import CurrentUserDep

router = APIRouter()


@router.post("/query")
async def query_library(current_user: CurrentUserDep):
    return {"message": "Query library endpoint", "user_id": str(current_user.id)}


@router.get("/conversations")
async def get_library_conversations(current_user: CurrentUserDep):
    return {
        "message": "Get library conversations endpoint",
        "user_id": str(current_user.id),
    }
