"""
Router: /lists
Handles creating, listing, and deleting grocery lists.
"""
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_db, q, fetchall, fetchone

router = APIRouter(prefix="/lists", tags=["lists"])


# ── MODELS ─────────────────────────────────────────────────────────────────────

class ListCreate(BaseModel):
    name: str

class ListOut(BaseModel):
    id: str
    name: str
    created_at: int


# ── ENDPOINTS ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ListOut])
def get_lists():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM lists ORDER BY created_at DESC")
    rows = fetchall(cur)
    cur.close(); conn.close()
    return rows


@router.post("", response_model=ListOut, status_code=201)
def create_list(body: ListCreate):
    list_id = str(uuid.uuid4())
    now     = int(time.time())
    conn    = get_db()
    cur     = conn.cursor()
    cur.execute(q("INSERT INTO lists (id, name, created_at) VALUES (?, ?, ?)"),
                (list_id, body.name.strip(), now))
    conn.commit()
    cur.execute(q("SELECT * FROM lists WHERE id = ?"), (list_id,))
    row = fetchone(cur)
    cur.close(); conn.close()
    return row


@router.delete("/{list_id}", status_code=204)
def delete_list(list_id: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(q("DELETE FROM lists WHERE id = ?"), (list_id,))
    conn.commit()
    affected = cur.rowcount
    cur.close(); conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="List not found")
