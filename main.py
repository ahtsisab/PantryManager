"""
Family Grocery List API
-----------------------
Entry point. Registers routers and runs DB initialisation on startup.

Start with:
    uvicorn main:app --reload
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
from routers import lists, items, pantry

app = FastAPI(
    title="Family Grocery List API",
    version="2.0.0",
    description="Grocery lists + pantry manager for the whole family.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── ROUTERS ────────────────────────────────────────────────────────────────────
app.include_router(lists.router)
app.include_router(items.router)
app.include_router(pantry.router)

# ── STARTUP ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    init_db()


# ── HEALTH CHECK ───────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
