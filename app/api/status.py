from fastapi import APIRouter

router = APIRouter(prefix="/status", tags=["status"])


@router.get("")
def get_status():
    return {"status": "idle"}
