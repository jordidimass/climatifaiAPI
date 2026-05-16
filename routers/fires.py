from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_fires():
    """Active fire events in the area of interest."""
    return {"fires": []}


@router.get("/risk")
async def fire_risk():
    """Aggregated fire risk score by grid cell."""
    return {"risk": []}
