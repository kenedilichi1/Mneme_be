from fastapi import APIRouter

router = APIRouter()

@router.post("")
async def upload_document():
    return {"message": "Upload document endpoint"}

@router.get("")
async def list_documents():
    return {"message": "List documents endpoint"}

@router.get("/{id}")
async def get_document(id: str):
    return {"message": f"Get document {id} endpoint"}

@router.delete("/{id}")
async def delete_document(id: str):
    return {"message": f"Delete document {id} endpoint"}

@router.post("/{id}/query")
async def query_document(id: str):
    return {"message": f"Query document {id} endpoint"}

@router.get("/{id}/conversations")
async def get_document_conversations(id: str):
    return {"message": f"Get document {id} conversations endpoint"}
