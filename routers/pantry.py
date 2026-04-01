"""
Router: /pantry + /categories
Handles pantry CRUD, category overrides, and auto-adding items to the Shopping list
when status changes to 'low' or 'out'.
"""
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    get_db, q, fetchall, fetchone,
    normalize_item, normalize_pantry,
    get_or_create_shopping_list,
)
from categories import CATEGORY_KEYWORDS, VALID_CATEGORIES, classify_item, classify_item_with_overrides, save_category_override

router = APIRouter(tags=["pantry"])

VALID_STATUSES = {"in_stock", "low", "out"}


# ── MODELS ─────────────────────────────────────────────────────────────────────

class PantryCreate(BaseModel):
    name: str
    quantity: Optional[str]  = "1"
    status: Optional[str]    = "in_stock"
    category: Optional[str]  = None   # None → auto-classify

class PantryUpdate(BaseModel):
    name: Optional[str]      = None
    quantity: Optional[str]  = None
    status: Optional[str]    = None   # in_stock | low | out
    category: Optional[str]  = None   # user override — persisted permanently

class PantryOut(BaseModel):
    id: str
    name: str
    quantity: str
    status: str
    category: str
    category_overridden: bool
    last_purchased_date: Optional[str]
    created_at: int

class CategoriesOut(BaseModel):
    categories: list[str]


# ── CATEGORY LISTING ───────────────────────────────────────────────────────────

@router.get("/categories", response_model=CategoriesOut)
def get_categories():
    """Return all available category names."""
    return {"categories": list(CATEGORY_KEYWORDS.keys())}


# ── AUTOCOMPLETE SUGGESTIONS ───────────────────────────────────────────────────

@router.get("/suggestions")
def get_suggestions():
    """
    Return autocomplete suggestions split into two lists:
      - history: distinct item names the user has actually added before (ranked first)
      - hardcoded: common grocery keywords from the classification table

    The frontend shows history items first, labelled "recent".
    """
    # Hardcoded keywords — multi-word and meaningful single words, title-cased
    hardcoded: set[str] = set()
    for keywords in CATEGORY_KEYWORDS.values():
        for kw in keywords:
            if " " in kw or len(kw) > 3:
                hardcoded.add(kw.title())

    # History from DB: all distinct names ever added to grocery lists or pantry
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT name FROM items WHERE name IS NOT NULL AND name != ''")
    history: set[str] = {row["name"] for row in fetchall(cur)}
    cur.execute("SELECT DISTINCT name FROM pantry WHERE name IS NOT NULL AND name != ''")
    history |= {row["name"] for row in fetchall(cur)}
    cur.close(); conn.close()

    # Remove hardcoded entries that are already in history (history copy takes precedence)
    hardcoded -= {h.title() for h in history}

    return {
        "history":   sorted(history,   key=lambda s: s.lower()),
        "hardcoded": sorted(hardcoded, key=lambda s: s.lower()),
    }


# ── PANTRY CRUD ────────────────────────────────────────────────────────────────

@router.get("/pantry", response_model=list[PantryOut])
def get_pantry():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM pantry ORDER BY name ASC")
    rows = fetchall(cur)
    cur.close(); conn.close()
    return [normalize_pantry(r) for r in rows]


@router.post("/pantry", response_model=PantryOut, status_code=201)
def add_pantry_item(body: PantryCreate):
    p_id   = str(uuid.uuid4())
    now    = int(time.time())
    status = body.status if body.status in VALID_STATUSES else "in_stock"

    if body.category and body.category in VALID_CATEGORIES:
        category   = body.category
        overridden = True
        save_category_override(body.name, body.category, get_db, q)
    else:
        category   = classify_item_with_overrides(body.name, get_db, q, fetchone)
        overridden = False

    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        q("INSERT INTO pantry "
          "(id, name, quantity, status, category, category_overridden, last_purchased_date, created_at) "
          "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)"),
        (p_id, body.name.strip(), (body.quantity or "1").strip() or "1",
         status, category, overridden, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (p_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return normalize_pantry(row)


@router.patch("/pantry/{pantry_id}")
def update_pantry_item(pantry_id: str, body: PantryUpdate):
    """
    Update a pantry item.
    - Category changes are treated as permanent user overrides.
    - Status transition -> low: auto-adds to Shopping list (no duplicates).
    - Status transition -> out: removes item from pantry entirely (qty = 0)
      and auto-adds to Shopping list.

    Returns: { pantry_item: PantryOut | None, grocery_item: ItemOut | None, deleted: bool }
    """
    conn = get_db()
    cur  = conn.cursor()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Pantry item not found")

    new_name     = body.name.strip()     if body.name     else existing["name"]
    new_quantity = body.quantity.strip() if body.quantity else existing["quantity"]
    new_status   = body.status           if body.status in VALID_STATUSES else existing["status"]

    # OUT: delete from pantry + add to shopping list
    if new_status == "out":
        cur.execute(q("DELETE FROM pantry WHERE id = ?"), (pantry_id,))
        conn.commit()
        grocery_item = _maybe_add_to_shopping(
            cur, conn,
            prev_status=existing["status"],
            new_status="out",
            name=new_name,
            quantity=new_quantity or "1",
        )
        cur.close(); conn.close()
        return {"pantry_item": None, "grocery_item": grocery_item, "deleted": True}

    # LOW / IN_STOCK: normal update
    new_category, new_overridden = _resolve_category(body, existing)

    cur.execute(
        q("UPDATE pantry SET name=?, quantity=?, status=?, category=?, category_overridden=? WHERE id=?"),
        (new_name, new_quantity or "1", new_status, new_category, new_overridden, pantry_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    updated_pantry = normalize_pantry(fetchone(cur))

    grocery_item = _maybe_add_to_shopping(
        cur, conn,
        prev_status=existing["status"],
        new_status=new_status,
        name=new_name,
        quantity=new_quantity or "1",
    )

    cur.close(); conn.close()
    return {"pantry_item": updated_pantry, "grocery_item": grocery_item, "deleted": False}


@router.delete("/pantry/{pantry_id}", status_code=204)
def delete_pantry_item(pantry_id: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(q("DELETE FROM pantry WHERE id = ?"), (pantry_id,))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Pantry item not found")


# ── PRIVATE HELPERS ────────────────────────────────────────────────────────────

def _resolve_category(body: PantryUpdate, existing: dict) -> tuple[str, bool]:
    """Return (category, overridden) for the update. Persists user overrides to DB."""
    if body.category and body.category in VALID_CATEGORIES:
        save_category_override(existing["name"], body.category, get_db, q)
        return body.category, True
    return (
        existing.get("category") or classify_item_with_overrides(existing["name"], get_db, q, fetchone),
        bool(existing.get("category_overridden")),
    )


def _maybe_add_to_shopping(cur, conn, prev_status: str,
                            new_status: str, name: str, quantity: str) -> Optional[dict]:
    """
    If status transitions from in_stock → low/out, add the item to the Shopping list.
    Returns the new grocery ItemOut dict, or None if no item was created.
    """
    if new_status not in ("low", "out") or prev_status == new_status:
        return None

    shopping = get_or_create_shopping_list(cur, conn)

    # Skip if an unpurchased item with the same name already exists
    cur.execute(
        q("SELECT id FROM items WHERE list_id = ? AND LOWER(name) = ? AND purchased = false"),
        (shopping["id"], name.lower())
    )
    if cur.fetchone():
        return None

    item_id = str(uuid.uuid4())
    now     = int(time.time())
    cur.execute(
        q("INSERT INTO items "
          "(id, list_id, name, quantity, purchased, purchased_date, created_at) "
          "VALUES (?, ?, ?, ?, false, NULL, ?)"),
        (item_id, shopping["id"], name, quantity, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    return normalize_item(fetchone(cur))
