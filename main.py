from fastapi import FastAPI

from routers import agri, fires, support

app = FastAPI(
    title="Climatifai / AgroRisk API",
    description="Agricultural risk intelligence — fires, climate, soil and support programs.",
    version="0.1.0",
)

app.include_router(fires.router, prefix="/fires", tags=["fires"])
app.include_router(agri.router, prefix="/agri", tags=["agri"])
app.include_router(support.router, prefix="/support", tags=["support"])


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
