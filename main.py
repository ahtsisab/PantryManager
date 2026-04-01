"""
Family Grocery List API
-----------------------
Entry point. Registers routers and runs DB initialisation on startup.

Start with:
    uvicorn main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import lists, items, pantry


# ── STARTUP / SHUTDOWN ────────────────────────────────────────────────────────
# Uses the modern lifespan pattern (replaces deprecated @app.on_event).
# init_db() is also called at module load time below as a safety net for
# environments where the lifespan hook fires too late (e.g. Railway cold starts).

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Family Grocery List API",
    version="2.0.0",
    description="Grocery lists + pantry manager for the whole family.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ROUTERS ───────────────────────────────────────────────────────────────────
app.include_router(lists.router)
app.include_router(items.router)
app.include_router(pantry.router)


# ── HEALTH CHECK ─────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


# ── MODULE-LEVEL INIT (safety net) ───────────────────────────────────────────
# Runs when uvicorn imports this module, before any request is served.
# Ensures tables exist even if the lifespan hook is skipped.
init_db()