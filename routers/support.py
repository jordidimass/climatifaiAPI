from fastapi import APIRouter

router = APIRouter()


@router.get("/programs")
async def list_programs():
    """Government support programs available for the region."""
    return {"programs": []}


@router.get("/programs/{program_id}")
async def get_program(program_id: str):
    """Detail of a specific government support program."""
    return {"program": None}
