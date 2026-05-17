import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from routers import agri, fires, support


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from db.session import dispose_engine, get_engine

    get_engine()
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(
    title="Climatifai / AgroRisk API",
    description="Agricultural risk intelligence — fires, climate, soil and support programs.",
    version="0.1.0",
    lifespan=lifespan,
)

_raw_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _raw_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fires.router, prefix="/fires", tags=["fires"])
app.include_router(agri.router, prefix="/agri", tags=["agri"])
app.include_router(support.router, prefix="/support", tags=["support"])


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
