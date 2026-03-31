from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import time
import uuid
import os
import re
from datetime import date

app = FastAPI(title="Family Grocery List API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DATABASE SETUP ─────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL")

# Hardcoded category classification table
# Maps category name → list of keywords (lowercase)
CATEGORY_KEYWORDS = {
    "Produce":    ["apple","banana","orange","grape","berry","berries","lettuce","spinach","kale",
                   "tomato","tomatoes","carrot","carrots","onion","onions","garlic","potato","potatoes",
                   "broccoli","cucumber","pepper","peppers","celery","mushroom","mushrooms","zucchini",
                   "avocado","lemon","lime","mango","melon","peach","pear","plum","strawberry",
                   "blueberry","raspberry","corn","peas","beans","herb","herbs","ginger","fruit","vegetable"],
    "Dairy":      ["milk","cheese","butter","yogurt","yoghurt","cream","egg","eggs","sour cream",
                   "cottage cheese","mozzarella","cheddar","parmesan","brie","feta","half and half",
                   "whipping cream","heavy cream","oat milk","almond milk","soy milk"],
    "Meat":       ["chicken","beef","pork","lamb","turkey","bacon","sausage","ham","steak","ground",
                   "fish","salmon","tuna","shrimp","seafood","lobster","crab","tilapia","cod","meat",
                   "deli","salami","pepperoni","prosciutto","veal","brisket","ribs","wings","drumstick"],
    "Bakery":     ["bread","bagel","muffin","croissant","roll","bun","cake","pie","cookie","cookies",
                   "donut","pastry","tortilla","pita","naan","sourdough","baguette","loaf","flour","yeast"],
    "Drinks":     ["water","juice","soda","pop","coffee","tea","beer","wine","spirits","vodka","whiskey",
                   "rum","gin","tequila","lemonade","kombucha","energy drink","sports drink","milk shake",
                   "smoothie","cider","sparkling","coconut water","drink","beverage"],
    "Frozen":     ["frozen","ice cream","gelato","sorbet","pizza","nugget","waffle","fries","edamame",
                   "ice","popsicle","frozen meal","frozen dinner","frozen vegetable","frozen fruit"],
    "Pantry":     ["rice","pasta","noodle","quinoa","oat","oatmeal","cereal","granola","soup","broth",
                   "stock","can","canned","sauce","salsa","ketchup","mustard","mayo","mayonnaise",
                   "oil","olive oil","vinegar","honey","jam","jelly","peanut butter","almond butter",
                   "nut butter","syrup","salt","pepper","spice","spices","seasoning","sugar","baking",
                   "chocolate","cocoa","vanilla","lentil","chickpea","bean","beans","coconut milk"],
    "Snacks":     ["chip","chips","cracker","crackers","popcorn","pretzel","nut","nuts","almond","cashew",
                   "walnut","peanut","trail mix","granola bar","protein bar","candy","chocolate bar",
                   "gummy","snack","jerky","dried fruit","raisin"],
    "Household":  ["soap","shampoo","conditioner","detergent","cleaner","bleach","sponge","paper towel",
                   "toilet paper","tissue","trash bag","garbage bag","foil","wrap","plastic wrap",
                   "zip bag","laundry","dish","dishwasher","toothpaste","toothbrush","deodorant",
                   "razor","lotion","sunscreen","vitamin","medicine","bandage","cleaning"],
    "Other":      [],  # catch-all
}


def classify_item(name: str) -> str:
    """Auto-classify an item name into a category based on keyword matching."""
    lower = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category == "Other":
            continue
        for kw in keywords:
            if kw in lower:
                return category
    return "Other"


def _try_add_quantities(existing_qty: str, new_qty: str) -> str:
    """
    Try to add two quantity strings numerically.
    e.g. "2" + "3" = "5", "500g" + "200g" = "700g"
    Falls back to new_qty if units mismatch or parsing fails.
    """
    def parse(q):
        q = q.strip()
        m = re.match(r'^(\d+\.?\d*)\s*([a-zA-Z]*)$', q)
        if m:
            return float(m.group(1)), m.group(2).lower()
        return None, None

    ev, eu = parse(existing_qty)
    nv, nu = parse(new_qty)

    if ev is not None and nv is not None:
        if eu == nu:  # same unit (or both unitless)
            total = ev + nv
            total_str = str(int(total)) if total == int(total) else str(total)
            return total_str + eu if eu else total_str

    # fallback: can't add — return new value
    return new_qty


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
                purchased_date TEXT,
                created_at BIGINT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pantry (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                status TEXT NOT NULL DEFAULT 'in_stock',
                category TEXT NOT NULL DEFAULT 'Other',
                category_overridden BOOLEAN NOT NULL DEFAULT FALSE,
                last_purchased_date TEXT,
                created_at BIGINT NOT NULL
            )
        """)
        # safe migrations for existing deployments
        migrations = [
            "ALTER TABLE items ADD COLUMN purchased_date TEXT",
            "ALTER TABLE pantry ADD COLUMN category TEXT NOT NULL DEFAULT 'Other'",
            "ALTER TABLE pantry ADD COLUMN category_overridden BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE pantry ADD COLUMN last_purchased_date TEXT",
        ]
        for stmt in migrations:
            try:
                cur.execute(stmt); conn.commit()
            except Exception:
                conn.rollback()
        conn.commit()
        cur.close(); conn.close()

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
                purchased_date TEXT,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS pantry (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL DEFAULT '1',
                status TEXT NOT NULL DEFAULT 'in_stock',
                category TEXT NOT NULL DEFAULT 'Other',
                category_overridden INTEGER NOT NULL DEFAULT 0,
                last_purchased_date TEXT,
                created_at INTEGER NOT NULL
            );
        """)
        migrations = [
            "ALTER TABLE items ADD COLUMN purchased_date TEXT",
            "ALTER TABLE pantry ADD COLUMN category TEXT NOT NULL DEFAULT 'Other'",
            "ALTER TABLE pantry ADD COLUMN category_overridden INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE pantry ADD COLUMN last_purchased_date TEXT",
        ]
        for stmt in migrations:
            try:
                conn.execute(stmt); conn.commit()
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
    purchased_date: Optional[str] = None   # ISO date string: "2025-04-01"

class ItemOut(BaseModel):
    id: str
    list_id: str
    name: str
    quantity: str
    purchased: bool
    purchased_date: Optional[str]
    created_at: int

class PantryCreate(BaseModel):
    name: str
    quantity: Optional[str] = "1"
    status: Optional[str] = "in_stock"
    category: Optional[str] = None        # if None, auto-classified

class PantryUpdate(BaseModel):
    name: Optional[str] = None
    quantity: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None        # user override — remembered permanently

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


# ── UTILITY ENDPOINTS ──────────────────────────────────────────────────────────

@app.get("/categories", response_model=CategoriesOut)
def get_categories():
    """Return the list of all available categories."""
    return {"categories": list(CATEGORY_KEYWORDS.keys())}


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
        q("INSERT INTO items (id, list_id, name, quantity, purchased, purchased_date, created_at) VALUES (?, ?, ?, ?, false, NULL, ?)"),
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
    Update purchased / quantity / purchased_date.
    When purchased=true:
      - purchased_date defaults to today if not provided
      - auto-upserts into pantry, ACCUMULATING quantity if item already exists
    Returns { item, pantry_item }
    """
    conn = get_db()
    cur = conn.cursor()
    _assert_list_exists(cur, list_id)

    cur.execute(q("SELECT * FROM items WHERE id = ? AND list_id = ?"), (item_id, list_id))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Item not found")

    was_purchased  = bool(existing["purchased"])
    new_purchased  = body.purchased  if body.purchased  is not None else was_purchased
    new_quantity   = (body.quantity.strip() or "1") if body.quantity is not None else existing["quantity"]

    # Resolve purchased_date
    if new_purchased:
        if body.purchased_date:
            new_pdate = body.purchased_date  # user-supplied ISO date
        elif not was_purchased:
            new_pdate = date.today().isoformat()  # default to today on first purchase
        else:
            new_pdate = existing.get("purchased_date")  # keep existing date
    else:
        new_pdate = None  # unpurchased — clear date

    cur.execute(
        q("UPDATE items SET purchased = ?, quantity = ?, purchased_date = ? WHERE id = ? AND list_id = ?"),
        (new_purchased, new_quantity, new_pdate, item_id, list_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM items WHERE id = ?"), (item_id,))
    updated_item = _normalize_item(fetchone(cur))

    pantry_item = None

    # Auto-upsert into pantry only when transitioning to purchased
    if new_purchased and not was_purchased:
        item_name_lower = existing["name"].strip().lower()
        cur.execute(q("SELECT * FROM pantry WHERE LOWER(name) = ?"), (item_name_lower,))
        existing_pantry = fetchone(cur)

        if existing_pantry:
            # ✅ FIXED: accumulate quantities instead of overwriting
            accumulated_qty = _try_add_quantities(
                str(existing_pantry["quantity"]), new_quantity
            )
            cur.execute(
                q("UPDATE pantry SET quantity = ?, status = 'in_stock', last_purchased_date = ? WHERE id = ?"),
                (accumulated_qty, new_pdate, existing_pantry["id"])
            )
            conn.commit()
            cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (existing_pantry["id"],))
        else:
            # New pantry entry — auto-classify unless already overridden
            category = classify_item(existing["name"])
            p_id = str(uuid.uuid4())
            now  = int(time.time())
            cur.execute(
                q("""INSERT INTO pantry
                       (id, name, quantity, status, category, category_overridden, last_purchased_date, created_at)
                     VALUES (?, ?, ?, 'in_stock', ?, false, ?, ?)"""),
                (p_id, existing["name"].strip(), new_quantity, category, new_pdate, now)
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
    p_id   = str(uuid.uuid4())
    now    = int(time.time())
    status = body.status if body.status in ("in_stock", "low", "out") else "in_stock"

    # Use provided category (user override) or auto-classify
    if body.category and body.category in CATEGORY_KEYWORDS:
        category   = body.category
        overridden = True
    else:
        category   = classify_item(body.name)
        overridden = False

    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        q("""INSERT INTO pantry
               (id, name, quantity, status, category, category_overridden, last_purchased_date, created_at)
             VALUES (?, ?, ?, ?, ?, ?, NULL, ?)"""),
        (p_id, body.name.strip(), (body.quantity or "1").strip() or "1",
         status, category, overridden, now)
    )
    conn.commit()
    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (p_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return _normalize_pantry(row)


@app.patch("/pantry/{pantry_id}")
def update_pantry_item(pantry_id: str, body: PantryUpdate):
    """
    Update pantry item fields.
    - If category is provided, it is treated as a permanent user override.
    - When status → low/out (from in_stock), auto-add to Shopping list (no duplicates).
    Returns { pantry_item, grocery_item }.
    """
    conn = get_db()
    cur  = conn.cursor()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    existing = fetchone(cur)
    if not existing:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Pantry item not found")

    new_name     = body.name.strip()     if body.name     else existing["name"]
    new_quantity  = body.quantity.strip() if body.quantity else existing["quantity"]
    new_status   = body.status           if body.status in ("in_stock", "low", "out") else existing["status"]

    # Category override — if user sets it, remember it permanently
    if body.category and body.category in CATEGORY_KEYWORDS:
        new_category   = body.category
        new_overridden = True
    else:
        new_category   = existing["category"] or classify_item(existing["name"])
        new_overridden = bool(existing["category_overridden"])

    cur.execute(
        q("UPDATE pantry SET name=?, quantity=?, status=?, category=?, category_overridden=? WHERE id=?"),
        (new_name, new_quantity or "1", new_status, new_category, new_overridden, pantry_id)
    )
    conn.commit()

    cur.execute(q("SELECT * FROM pantry WHERE id = ?"), (pantry_id,))
    updated_pantry = _normalize_pantry(fetchone(cur))

    grocery_item = None
    prev_status  = existing["status"]

    # Auto-add to Shopping list when transitioning from in_stock → low/out
    if new_status in ("low", "out") and prev_status == "in_stock":
        shopping_list = _get_or_create_shopping_list(cur, conn)
        cur.execute(
            q("SELECT * FROM items WHERE list_id = ? AND LOWER(name) = ? AND purchased = false"),
            (shopping_list["id"], new_name.lower())
        )
        if not fetchone(cur):
            item_id = str(uuid.uuid4())
            now = int(time.time())
            cur.execute(
                q("INSERT INTO items (id, list_id, name, quantity, purchased, purchased_date, created_at) VALUES (?, ?, ?, ?, false, NULL, ?)"),
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
    cur  = conn.cursor()
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
    row["purchased"]      = bool(row["purchased"])
    row["quantity"]       = str(row.get("quantity") or "1")
    row["purchased_date"] = row.get("purchased_date")
    return row

def _normalize_pantry(row: dict) -> dict:
    row["quantity"]           = str(row.get("quantity") or "1")
    row["status"]             = row.get("status") or "in_stock"
    row["category"]           = row.get("category") or "Other"
    row["category_overridden"]= bool(row.get("category_overridden"))
    row["last_purchased_date"]= row.get("last_purchased_date")
    return row

def _get_or_create_shopping_list(cur, conn) -> dict:
    cur.execute(q("SELECT * FROM lists WHERE LOWER(name) = 'shopping' ORDER BY created_at ASC LIMIT 1"), ())
    row = fetchone(cur)
    if row:
        return row
    list_id = str(uuid.uuid4())
    now = int(time.time())
    cur.execute(q("INSERT INTO lists (id, name, created_at) VALUES (?, ?, ?)"),
                (list_id, "Shopping", now))
    conn.commit()
    cur.execute(q("SELECT * FROM lists WHERE id = ?"), (list_id,))
    return fetchone(cur)
