from fastapi import APIRouter

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("")
def upload_document():
    return {"message": "Upload endpoint ready"}
