"""
Router: /lists/{list_id}/items
Handles adding, updating, and deleting grocery items.
When an item is marked purchased:
  - Records the purchase date (defaults to today)
  - Auto-upserts the item into the pantry, accumulating quantity if it already exists
"""
import time
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    get_db, q, fetchall, fetchone,
    assert_list_exists, normalize_item, normalize_pantry,
)
from categories import classify_item, try_add_quantities

router = APIRouter(prefix="/lists/{list_id}/items", tags=["items"])


# ── MODELS ─────────────────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    name: str
    quantity: Optional[str] = "1"

class ItemUpdate(BaseModel):
    purchased: Optional[bool] = None
    quantity: Optional[str]   = None
    purchased_date: Optional[str] = None   # ISO date: "2025-04-01"

class ItemOut(BaseModel):
    id: str
    list_id: str
    name: str
    quantity: str
    purchased: bool
    purchased_date: Optional[str]
    created_at: int


# ── ENDPOINTS ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ItemOut])
def get_items(list_id: str):
    conn = get_db()
    cur  = conn.cursor()
    assert_list_exists(cur, list_id)
    cur.execute(q("SELECT * FROM items WHERE list_id = ? ORDER BY created_at ASC"), (list_id,))
    rows = fetchall(cur)
    cur.close(); conn.close()
    return [normalize_item(r) for r in rows]


@router.post("", response_model=ItemOut, status_code=201)
def add_item(list_id: str, body: ItemCreate):
    item_id  = str(uuid.uuid4())
    now      = int(time.time())
    quantity = (body.quantity or "1").strip() or "1"
    conn     = get_db()
    cur      = conn.cursor()
    assert_list_exists(cur, list_id)
    cur.execute(
        q("INSERT INTO items (id, list_id, name, quantity, purchased, purchased_date, created_at) "
          "VALUES (?, ?, ?, ?, false, NULL, ?)"),
        (item_id, list_id, body.name.strip(), quantity, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return normalize_item(row)


@router.patch("/{item_id}")
def update_item(list_id: str, item_id: str, body: ItemUpdate):
    """
    Update purchased state, quantity, and/or purchase date.

    On transition to purchased=True:
      - Sets purchased_date to body.purchased_date, or today if omitted.
      - Upserts the item into the pantry:
          * Existing pantry entry → accumulate quantity + reset status to in_stock.
          * New entry → auto-classify category.

    Returns: { item: ItemOut, pantry_item: PantryOut | None }
    """
    conn = get_db()
    cur  = conn.cursor()
    assert_list_exists(cur, list_id)

    cur.execute(q("SELECT * FROM items WHERE id = ? AND list_id = ?"), (item_id, list_id))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Item not found")

    was_purchased = bool(existing["purchased"])
    new_purchased = body.purchased if body.purchased is not None else was_purchased
    new_quantity  = (body.quantity.strip() or "1") if body.quantity is not None else existing["quantity"]
    new_pdate     = _resolve_purchase_date(body, existing, was_purchased, new_purchased)

    cur.execute(
        q("UPDATE items SET purchased = ?, quantity = ?, purchased_date = ? WHERE id = ? AND list_id = ?"),
        (new_purchased, new_quantity, new_pdate, item_id, list_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    updated_item = normalize_item(fetchone(cur))

    pantry_item = None
    if new_purchased and not was_purchased:
        pantry_item = _upsert_pantry(cur, conn, existing, new_quantity, new_pdate)

    cur.close(); conn.close()
    return {"item": updated_item, "pantry_item": pantry_item}


@router.delete("/{item_id}", status_code=204)
def delete_item(list_id: str, item_id: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(q("DELETE FROM items WHERE id = ? AND list_id = ?"), (item_id, list_id))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Item not found")


# ── PRIVATE HELPERS ────────────────────────────────────────────────────────────

def _resolve_purchase_date(body: ItemUpdate, existing: dict,
                            was_purchased: bool, new_purchased: bool) -> Optional[str]:
    """Work out the correct purchased_date for the updated item."""
    if not new_purchased:
        return None  # unchecked — clear date
    if body.purchased_date:
        return body.purchased_date  # user-supplied override
    if not was_purchased:
        return date.today().isoformat()  # first-time purchase → default to today
    return existing.get("purchased_date")  # already purchased — keep existing date


def _upsert_pantry(cur, conn, grocery_item: dict,
                   quantity: str, purchased_date: Optional[str]) -> dict:
    """
    Insert or update a pantry row when a grocery item is checked off.
    Accumulates quantity if the item already exists in the pantry.
    """
    name_lower = grocery_item["name"].strip().lower()
    cur.execute(q("SELECT * FROM pantry WHERE LOWER(name) = ?"), (name_lower,))
    existing_pantry = fetchone(cur)

    if existing_pantry:
        accumulated = try_add_quantities(
            str(existing_pantry["quantity"]), quantity
        )
        cur.execute(
            q("UPDATE pantry SET quantity = ?, status = 'in_stock', last_purchased_date = ? WHERE id = ?"),
            (accumulated, purchased_date, existing_pantry["id"])
        )
        conn.commit()
        cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (existing_pantry["id"],))
    else:
        category = classify_item(grocery_item["name"])
        p_id     = str(uuid.uuid4())
        now      = int(time.time())
        cur.execute(
            q("INSERT INTO pantry "
              "(id, name, quantity, status, category, category_overridden, last_purchased_date, created_at) "
              "VALUES (?, ?, ?, 'in_stock', ?, false, ?, ?)"),
            (p_id, grocery_item["name"].strip(), quantity, category, purchased_date, now)
        )
        conn.commit()
        cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (p_id,))

    return normalize_pantry(fetchone(cur))
