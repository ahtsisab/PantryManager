from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import time
import uuid
import os

app = FastAPI(title="Family Grocery List API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DATABASE SETUP ─────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    def get_db():
        return psycopg2.connect(DATABASE_URL)

    def q(sql):
        return sql.replace("?", "%s")

    def fetchall(cur):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetchone(cur):
        if not cur.description:
            return None
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    def init_db():
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at BIGINT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                purchased BOOLEAN NOT NULL DEFAULT FALSE,
                created_at BIGINT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pantry (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                status TEXT NOT NULL DEFAULT 'in_stock',
                created_at BIGINT NOT NULL
            )
        """)
        # safe migrations
        for stmt in [
            "ALTER TABLE items ADD COLUMN quantity TEXT NOT NULL DEFAULT '1'",
        ]:
            try:
                cur.execute(stmt)
                conn.commit()
            except Exception:
                conn.rollback()
        conn.commit()
        cur.close()
        conn.close()

else:
    import sqlite3

    DB_PATH = "grocery.db"

    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def q(sql):
        return sql

    def fetchall(cur):
        return [dict(r) for r in cur.fetchall()]

    def fetchone(cur):
        row = cur.fetchone()
        return dict(row) if row else None

    def init_db():
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                list_id TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                purchased INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS pantry (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                status TEXT NOT NULL DEFAULT 'in_stock',
                created_at INTEGER NOT NULL
            );
        """)
        for stmt in [
            "ALTER TABLE items ADD COLUMN quantity TEXT NOT NULL DEFAULT '1'",
        ]:
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception:
                pass
        conn.commit()
        conn.close()


init_db()


# ── PYDANTIC MODELS ────────────────────────────────────────────────────────────

class ListCreate(BaseModel):
    name: str

class ListOut(BaseModel):
    id: str
    name: str
    created_at: int

class ItemCreate(BaseModel):
    name: str
    quantity: Optional[str] = "1"

class ItemUpdate(BaseModel):
    purchased: Optional[bool] = None
    quantity: Optional[str] = None

class ItemOut(BaseModel):
    id: str
    list_id: str
    name: str
    quantity: str
    purchased: bool
    created_at: int

class ItemPurchasedResponse(BaseModel):
    item: ItemOut
    pantry_item: Optional[dict] = None

class PantryCreate(BaseModel):
    name: str
    quantity: Optional[str] = "1"
    status: Optional[str] = "in_stock"

class PantryUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[str] = None
    status: Optional[str] = None  # in_stock | low | out

class PantryOut(BaseModel):
    id: str
    name: str
    quantity: str
    status: str
    created_at: int

class PantryStatusResponse(BaseModel):
    pantry_item: PantryOut
    grocery_item: Optional[ItemOut] = None  # set if auto-added to Shopping list


# ── LIST ENDPOINTS ─────────────────────────────────────────────────────────────

@app.get("/lists", response_model=list[ListOut])
def get_lists():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM lists ORDER BY created_at DESC")
    rows = fetchall(cur)
    cur.close(); conn.close()
    return rows


@app.post("/lists", response_model=ListOut, status_code=201)
def create_list(body: ListCreate):
    list_id = str(uuid.uuid4())
    now = int(time.time())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("INSERT INTO lists (id, name, created_at) VALUES (?, ?, ?)"),
                (list_id, body.name.strip(), now))
    conn.commit()
    cur.execute(q("SELECT * FROM lists WHERE id = ?"), (list_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return row


@app.delete("/lists/{list_id}", status_code=204)
def delete_list(list_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("DELETE FROM lists WHERE id = ?"), (list_id,))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="List not found")


# ── ITEM ENDPOINTS ─────────────────────────────────────────────────────────────

@app.get("/lists/{list_id}/items", response_model=list[ItemOut])
def get_items(list_id: str):
    conn = get_db()
    cur = conn.cursor()
    _assert_list_exists(cur, list_id)
    cur.execute(q("SELECT * FROM items WHERE list_id = ? ORDER BY created_at ASC"), (list_id,))
    rows = fetchall(cur)
    cur.close(); conn.close()
    return [_normalize_item(r) for r in rows]


@app.post("/lists/{list_id}/items", response_model=ItemOut, status_code=201)
def add_item(list_id: str, body: ItemCreate):
    item_id = str(uuid.uuid4())
    now = int(time.time())
    quantity = (body.quantity or "1").strip() or "1"
    conn = get_db()
    cur = conn.cursor()
    _assert_list_exists(cur, list_id)
    cur.execute(
        q("INSERT INTO items (id, list_id, name, quantity, purchased, created_at) VALUES (?, ?, ?, ?, false, ?)"),
        (item_id, list_id, body.name.strip(), quantity, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return _normalize_item(row)


@app.patch("/lists/{list_id}/items/{item_id}")
def update_item(list_id: str, item_id: str, body: ItemUpdate):
    """
    Update purchased/quantity. When purchased=true, auto-upserts item into pantry.
    Returns { item, pantry_item } so frontend knows what happened.
    """
    conn = get_db()
    cur = conn.cursor()
    _assert_list_exists(cur, list_id)

    # fetch current item
    cur.execute(q("SELECT * FROM items WHERE id = ? AND list_id = ?"), (item_id, list_id))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Item not found")

    new_purchased = body.purchased if body.purchased is not None else bool(existing["purchased"])
    new_quantity  = (body.quantity.strip() or "1") if body.quantity is not None else existing["quantity"]

    cur.execute(
        q("UPDATE items SET purchased = ?, quantity = ? WHERE id = ? AND list_id = ?"),
        (new_purchased, new_quantity, item_id, list_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    updated_item = _normalize_item(fetchone(cur))

    pantry_item = None

    # Auto-upsert into pantry when marking purchased
    if new_purchased and not bool(existing["purchased"]):
        item_name = existing["name"].strip().lower()
        cur.execute(q("SELECT * FROM pantry WHERE LOWER(name) = ?"), (item_name,))
        existing_pantry = fetchone(cur)

        if existing_pantry:
            # update quantity + reset status to in_stock
            cur.execute(
                q("UPDATE pantry SET quantity = ?, status = 'in_stock' WHERE id = ?"),
                (new_quantity, existing_pantry["id"])
            )
            conn.commit()
            cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (existing_pantry["id"],))
        else:
            p_id = str(uuid.uuid4())
            now  = int(time.time())
            cur.execute(
                q("INSERT INTO pantry (id, name, quantity, status, created_at) VALUES (?, ?, ?, 'in_stock', ?)"),
                (p_id, existing["name"].strip(), new_quantity, now)
            )
            conn.commit()
            cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (p_id,))

        pantry_item = _normalize_pantry(fetchone(cur))

    cur.close(); conn.close()
    return {"item": updated_item, "pantry_item": pantry_item}


@app.delete("/lists/{list_id}/items/{item_id}", status_code=204)
def delete_item(list_id: str, item_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("DELETE FROM items WHERE id = ? AND list_id = ?"), (item_id, list_id))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Item not found")


# ── PANTRY ENDPOINTS ───────────────────────────────────────────────────────────

@app.get("/pantry", response_model=list[PantryOut])
def get_pantry():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pantry ORDER BY name ASC")
    rows = fetchall(cur)
    cur.close(); conn.close()
    return [_normalize_pantry(r) for r in rows]


@app.post("/pantry", response_model=PantryOut, status_code=201)
def add_pantry_item(body: PantryCreate):
    p_id = str(uuid.uuid4())
    now  = int(time.time())
    status = body.status if body.status in ("in_stock", "low", "out") else "in_stock"
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        q("INSERT INTO pantry (id, name, quantity, status, created_at) VALUES (?, ?, ?, ?, ?)"),
        (p_id, body.name.strip(), (body.quantity or "1").strip() or "1", status, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (p_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return _normalize_pantry(row)


@app.patch("/pantry/{pantry_id}")
def update_pantry_item(pantry_id: str, body: PantryUpdate):
    """
    Update pantry item. When status changes to 'low' or 'out',
    auto-adds to the 'Shopping' list (created if needed, no duplicates).
    Returns { pantry_item, grocery_item }.
    """
    conn = get_db()
    cur = conn.cursor()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Pantry item not found")

    new_name     = body.name.strip()     if body.name     else existing["name"]
    new_quantity = body.quantity.strip() if body.quantity else existing["quantity"]
    new_status   = body.status           if body.status in ("in_stock", "low", "out") else existing["status"]

    cur.execute(
        q("UPDATE pantry SET name = ?, quantity = ?, status = ? WHERE id = ?"),
        (new_name, new_quantity or "1", new_status, pantry_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    updated_pantry = _normalize_pantry(fetchone(cur))

    grocery_item = None
    prev_status  = existing["status"]

    # Auto-add to Shopping list when status becomes low or out
    if new_status in ("low", "out") and prev_status == "in_stock":
        shopping_list = _get_or_create_shopping_list(cur, conn)

        # Check for duplicate (case-insensitive)
        cur.execute(
            q("SELECT * FROM items WHERE list_id = ? AND LOWER(name) = ? AND purchased = false"),
            (shopping_list["id"], new_name.lower())
        )
        dupe = fetchone(cur)

        if not dupe:
            item_id = str(uuid.uuid4())
            now = int(time.time())
            cur.execute(
                q("INSERT INTO items (id, list_id, name, quantity, purchased, created_at) VALUES (?, ?, ?, ?, false, ?)"),
                (item_id, shopping_list["id"], new_name, new_quantity or "1", now)
            )
            conn.commit()
            cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
            grocery_item = _normalize_item(fetchone(cur))

    cur.close(); conn.close()
    return {"pantry_item": updated_pantry, "grocery_item": grocery_item}


@app.delete("/pantry/{pantry_id}", status_code=204)
def delete_pantry_item(pantry_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("DELETE FROM pantry WHERE id = ?"), (pantry_id,))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Pantry item not found")


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _assert_list_exists(cur, list_id: str):
    cur.execute(q("SELECT id FROM lists WHERE id = ?"), (list_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="List not found")

def _normalize_item(row: dict) -> dict:
    row["purchased"] = bool(row["purchased"])
    row["quantity"]  = str(row.get("quantity") or "1")
    return row

def _normalize_pantry(row: dict) -> dict:
    row["quantity"] = str(row.get("quantity") or "1")
    row["status"]   = row.get("status") or "in_stock"
    return row

def _get_or_create_shopping_list(cur, conn) -> dict:
    """Get the 'Shopping' list, creating it if it doesn't exist."""
    cur.execute(q("SELECT * FROM lists WHERE LOWER(name) = 'shopping' ORDER BY created_at ASC LIMIT 1"), ())
    row = fetchone(cur)
    if row:
        return row
    list_id = str(uuid.uuid4())
    now = int(time.time())
    cur.execute(
        q("INSERT INTO lists (id, name, created_at) VALUES (?, ?, ?)"),
        (list_id, "Shopping", now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM lists WHERE id = ?"), (list_id,))
    return fetchone(cur)
