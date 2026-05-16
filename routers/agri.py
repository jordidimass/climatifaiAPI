from fastapi import APIRouter

router = APIRouter()


@router.get("/score")
async def agri_score():
    """Agricultural risk score for a region and crop."""
    return {"score": None}


@router.get("/climate")
async def climate_normals():
    """Historical climate normals for a grid cell."""
    return {"climate": []}


@router.get("/soil")
async def soil_data():
    """Soil properties from SoilGrids for a location."""
    return {"soil": {}}
