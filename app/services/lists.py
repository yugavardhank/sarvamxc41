"""Named saved grocery lists: a customer can say "save this as my Sunday
list" after building a basket, then later "order from my Sunday list" to
reload it in one line instead of reciting every item again.
"""
from app.db import get_conn, log_event, now


def _normalize(name: str) -> str:
    return name.strip().lower()


def save_list(customer_id: int, name: str, items: list[dict]) -> int:
    """Creates the list if it's new, or replaces its items if a list with
    this name already exists for the customer (saving again just updates it).
    """
    normalized = _normalize(name)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM saved_lists WHERE customer_id = ? AND LOWER(name) = ?",
            (customer_id, normalized),
        ).fetchone()
        if existing:
            list_id = existing["id"]
            conn.execute("DELETE FROM saved_list_items WHERE list_id = ?", (list_id,))
        else:
            cur = conn.execute(
                "INSERT INTO saved_lists (customer_id, name, created_at) VALUES (?, ?, ?)",
                (customer_id, name.strip(), now()),
            )
            list_id = cur.lastrowid

        conn.executemany(
            """
            INSERT INTO saved_list_items (list_id, requested_text, matched_sku, category, brand, qty)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (list_id, item["requested_text"], item["matched_sku"], item["category"], item.get("brand"), item["qty"])
                for item in items
                if item.get("matched_sku")
            ],
        )
    log_event("list_saved", customer_id, f"list={list_id} name={name} items={len(items)}")
    return list_id


def list_saved_lists(customer_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_lists WHERE customer_id = ? ORDER BY id DESC", (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_list_items(list_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM saved_list_items WHERE list_id = ?", (list_id,)).fetchall()
    return [dict(r) for r in rows]


def find_list_by_name(customer_id: int, name: str) -> dict | None:
    """Fuzzy match: exact match first, then substring either direction, so
    "usual" matches a list named "My Usual Order" and vice versa.
    """
    normalized = _normalize(name)
    lists = list_saved_lists(customer_id)
    for saved in lists:
        if _normalize(saved["name"]) == normalized:
            return saved
    for saved in lists:
        saved_name = _normalize(saved["name"])
        if normalized in saved_name or saved_name in normalized:
            return saved
    return None
