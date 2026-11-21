from app.db import get_conn, log_event, now
from app.services import policy

CONFIRMED_STATUSES = policy.CONFIRMED_STATUSES


def list_customers() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_customer(customer_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


def touch_last_called(customer_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE customers SET last_called_at = ? WHERE id = ?", (now(), customer_id))


def debit_wallet(customer_id: int, amount: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE customers SET wallet_balance = wallet_balance - ? WHERE id = ?", (amount, customer_id))
    log_event("wallet_debit", customer_id, f"amount={amount}")


def credit_wallet(customer_id: int, amount: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE customers SET wallet_balance = wallet_balance + ? WHERE id = ?", (amount, customer_id))
    log_event("wallet_credit", customer_id, f"amount={amount}")


def add_outstanding(customer_id: int, delta: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE customers SET outstanding = outstanding + ? WHERE id = ?", (delta, customer_id))


def get_last_confirmed_order(customer_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM orders WHERE customer_id = ? AND status IN {CONFIRMED_STATUSES}
            ORDER BY id DESC LIMIT 1
            """,
            (customer_id,),
        ).fetchone()
    return dict(row) if row else None


def create_order(customer_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders (customer_id, status, created_at) VALUES (?, 'REQUESTED', ?)",
            (customer_id, now()),
        )
        order_id = cur.lastrowid
    log_event("order_created", customer_id, f"order={order_id}")
    return order_id


def get_order(order_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def list_orders() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT o.*, c.name AS customer_name, c.phone AS customer_phone
            FROM orders o JOIN customers c ON c.id = o.customer_id
            ORDER BY o.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_order_items(order_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order_id,)).fetchall()
    return [dict(r) for r in rows]


def add_order_item(order_id: int, requested_text: str, matched_sku: str | None, category: str | None, brand: str | None, qty: int, unit_price: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO order_items (order_id, requested_text, matched_sku, category, brand, qty, unit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, requested_text, matched_sku, category, brand, qty, unit_price),
        )
        return cur.lastrowid


def update_order_item_sku(item_id: int, new_sku: str, new_unit_price: int, new_brand: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE order_items SET matched_sku = ?, unit_price = ?, brand = COALESCE(?, brand) WHERE id = ?",
            (new_sku, new_unit_price, new_brand, item_id),
        )


def recompute_subtotal(order_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(qty * unit_price), 0) AS subtotal FROM order_items WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return row["subtotal"]


def _delivery_fee_for(subtotal: int, item_count: int) -> int:
    if item_count == 0:
        return 0  # nothing to deliver
    return 0 if subtotal >= policy.FREE_DELIVERY_THRESHOLD else policy.DELIVERY_FEE


def replace_order_items(order_id: int, matched_items: list[dict]) -> int:
    """Clears any previous basket for this order and inserts the new one,
    then recomputes subtotal/delivery_fee/total. Returns the new total.
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        conn.executemany(
            """
            INSERT INTO order_items (order_id, requested_text, matched_sku, category, brand, qty, unit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    order_id,
                    item["requested_text"],
                    item["matched_sku"],
                    item["category"],
                    item.get("brand"),
                    item["qty"],
                    item["unit_price"],
                )
                for item in matched_items
            ],
        )
    subtotal = recompute_subtotal(order_id)
    delivery_fee = _delivery_fee_for(subtotal, len(matched_items))
    total = subtotal + delivery_fee
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET subtotal = ?, delivery_fee = ?, discount_total = 0, total = ? WHERE id = ?",
            (subtotal, delivery_fee, total, order_id),
        )
    return total


def set_pricing(order_id: int, subtotal: int | None = None, delivery_fee: int | None = None, discount_total: int | None = None) -> int:
    """Updates whichever pricing fields are given and recomputes total.
    Returns the new total.
    """
    order = get_order(order_id)
    new_subtotal = subtotal if subtotal is not None else order["subtotal"]
    new_delivery_fee = delivery_fee if delivery_fee is not None else order["delivery_fee"]
    new_discount_total = discount_total if discount_total is not None else order["discount_total"]
    total = max(0, new_subtotal + new_delivery_fee - new_discount_total)
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET subtotal = ?, delivery_fee = ?, discount_total = ?, total = ? WHERE id = ?",
            (new_subtotal, new_delivery_fee, new_discount_total, total, order_id),
        )
    return total


def bump_offered_rung(order_id: int, rung_index: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE orders SET offered_rung = ? WHERE id = ?", (rung_index, order_id))


def set_status(order_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    log_event("order_status", detail=f"order={order_id} status={status}")


def set_payment_plan(order_id: int, pay_now: int, outstanding_new: int, wallet_applied: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE orders SET pay_now = ?, outstanding_new = ?, wallet_applied = ? WHERE id = ?",
            (pay_now, outstanding_new, wallet_applied, order_id),
        )


def set_payment_link(order_id: int, link_id: str, link_url: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE orders SET link_id = ?, link_url = ? WHERE id = ?", (link_id, link_url, order_id))


def get_order_by_link_id(link_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM orders WHERE link_id = ?", (link_id,)).fetchone()
    return dict(row) if row else None


def get_concessions(order_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM concessions WHERE order_id = ? ORDER BY id", (order_id,)
        ).fetchall()
    return [dict(r) for r in rows]
