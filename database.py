"""
Database layer.
Auto-detects DATABASE_URL (Postgres on Railway) and falls back to SQLite locally.
Exposes: get_db(), q(), fetchall(), fetchone(), init_db()
"""
import os
import time
import uuid

DATABASE_URL = os.environ.get("DATABASE_URL")

# ── POSTGRES ───────────────────────────────────────────────────────────────────
if DATABASE_URL:
    import psycopg2

    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    def get_db():
        return psycopg2.connect(DATABASE_URL)

    def q(sql: str) -> str:
        """Translate SQLite ? placeholders to Postgres %s."""
        return sql.replace("?", "%s")

    def fetchall(cur) -> list[dict]:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def fetchone(cur) -> dict | None:
        if not cur.description:
            return None
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None

    def init_db() -> None:
        import time as _time
        # Retry loop: Postgres on Railway may not be ready immediately at startup
        last_err = None
        for attempt in range(10):
            try:
                conn = get_db()
                break
            except Exception as e:
                last_err = e
                _time.sleep(2)
        else:
            raise RuntimeError(f"Could not connect to Postgres after retries: {last_err}")

        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lists (
                id          TEXT PRIMARY KEY,
                name        TEXT    NOT NULL,
                created_at  BIGINT  NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id              TEXT    PRIMARY KEY,
                list_id         TEXT    NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
                name            TEXT    NOT NULL,
                quantity        TEXT    NOT NULL DEFAULT '1',
                purchased       BOOLEAN NOT NULL DEFAULT FALSE,
                purchased_date  TEXT,
                created_at      BIGINT  NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pantry (
                id                   TEXT    PRIMARY KEY,
                name                 TEXT    NOT NULL,
                quantity             TEXT    NOT NULL DEFAULT '1',
                status               TEXT    NOT NULL DEFAULT 'in_stock',
                category             TEXT    NOT NULL DEFAULT 'Other',
                category_overridden  BOOLEAN NOT NULL DEFAULT FALSE,
                last_purchased_date  TEXT,
                created_at           BIGINT  NOT NULL
            )
        """)
        # Commit CREATE TABLE statements before running migrations.
        # Postgres requires tables to be visible before ALTER TABLE can reference them.
        conn.commit()
        _run_migrations_pg(cur, conn)
        cur.close()
        conn.close()

    def _run_migrations_pg(cur, conn) -> None:
        migrations = [
            "ALTER TABLE items   ADD COLUMN purchased_date       TEXT",
            "ALTER TABLE pantry  ADD COLUMN category             TEXT NOT NULL DEFAULT 'Other'",
            "ALTER TABLE pantry  ADD COLUMN category_overridden  BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE pantry  ADD COLUMN last_purchased_date  TEXT",
        ]
        for stmt in migrations:
            try:
                cur.execute(stmt)
                conn.commit()
            except Exception:
                conn.rollback()

# ── SQLITE (local dev) ─────────────────────────────────────────────────────────
else:
    import sqlite3

    DB_PATH = "grocery.db"

    def get_db():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def q(sql: str) -> str:
        return sql  # SQLite uses ? natively

    def fetchall(cur) -> list[dict]:
        return [dict(r) for r in cur.fetchall()]

    def fetchone(cur) -> dict | None:
        row = cur.fetchone()
        return dict(row) if row else None

    def init_db() -> None:
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lists (
                id          TEXT    PRIMARY KEY,
                name        TEXT    NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS items (
                id              TEXT    PRIMARY KEY,
                list_id         TEXT    NOT NULL,
                name            TEXT    NOT NULL,
                quantity        TEXT    NOT NULL DEFAULT '1',
                purchased       INTEGER NOT NULL DEFAULT 0,
                purchased_date  TEXT,
                created_at      INTEGER NOT NULL,
                FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS pantry (
                id                   TEXT    PRIMARY KEY,
                name                 TEXT    NOT NULL,
                quantity             TEXT    NOT NULL DEFAULT '1',
                status               TEXT    NOT NULL DEFAULT 'in_stock',
                category             TEXT    NOT NULL DEFAULT 'Other',
                category_overridden  INTEGER NOT NULL DEFAULT 0,
                last_purchased_date  TEXT,
                created_at           INTEGER NOT NULL
            );
        """)
        _run_migrations_sqlite(conn)
        conn.commit()
        conn.close()

    def _run_migrations_sqlite(conn) -> None:
        migrations = [
            "ALTER TABLE items   ADD COLUMN purchased_date       TEXT",
            "ALTER TABLE pantry  ADD COLUMN category             TEXT NOT NULL DEFAULT 'Other'",
            "ALTER TABLE pantry  ADD COLUMN category_overridden  INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE pantry  ADD COLUMN last_purchased_date  TEXT",
        ]
        for stmt in migrations:
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception:
                pass


# ── SHARED ROW NORMALIZERS ─────────────────────────────────────────────────────

def normalize_item(row: dict) -> dict:
    row["purchased"]      = bool(row["purchased"])
    row["quantity"]       = str(row.get("quantity") or "1")
    row["purchased_date"] = row.get("purchased_date")
    return row


def normalize_pantry(row: dict) -> dict:
    row["quantity"]            = str(row.get("quantity") or "1")
    row["status"]              = row.get("status") or "in_stock"
    row["category"]            = row.get("category") or "Other"
    row["category_overridden"] = bool(row.get("category_overridden"))
    row["last_purchased_date"] = row.get("last_purchased_date")
    return row


# ── SHARED DB HELPERS ──────────────────────────────────────────────────────────

def assert_list_exists(cur, list_id: str) -> None:
    from fastapi import HTTPException
    cur.execute(q("SELECT id FROM lists WHERE id = ?"), (list_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="List not found")


def get_or_create_shopping_list(cur, conn) -> dict:
    """Return the 'Shopping' list row, creating it if it doesn't exist."""
    cur.execute(q("SELECT * FROM lists WHERE LOWER(name) = 'shopping' ORDER BY created_at ASC LIMIT 1"), ())
    row = fetchone(cur)
    if row:
        return row
    list_id = str(uuid.uuid4())
    now     = int(time.time())
    cur.execute(q("INSERT INTO lists (id, name, created_at) VALUES (?, ?, ?)"),
                (list_id, "Shopping", now))
    conn.commit()
    cur.execute(q("SELECT * FROM lists WHERE id = ?"), (list_id,))
    return fetchone(cur)