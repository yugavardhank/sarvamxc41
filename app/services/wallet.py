"""Tokri's prepaid wallet: a customer tops up through a normal Cashfree
payment link, and Tokri keeps the resulting balance as a self-tracked ledger
(not a separate Cashfree product). Orders draw down this balance first,
before falling back to a fresh payment link or on-account credit - the point
being retention: a customer with money already sitting in Tokri has a reason
to keep calling Tokri instead of drifting to an app.
"""
from app.db import get_conn, log_event, now
from app.services import cashfree, orders


def create_topup(customer_id: int, amount: int) -> dict:
    customer = orders.get_customer(customer_id)
    link = cashfree.create_payment_link(
        amount=amount, customer_phone=customer["phone"], purpose=f"Tokri wallet top-up for {customer['name']}"
    )
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO wallet_topups (customer_id, amount, link_id, link_url, status, created_at) VALUES (?, ?, ?, ?, 'PENDING', ?)",
            (customer_id, amount, link["link_id"], link["link_url"], now()),
        )
        topup_id = cur.lastrowid
    log_event("wallet_topup_created", customer_id, f"topup={topup_id} amount={amount} link={link['link_id']}")
    return {"topup_id": topup_id, "link_id": link["link_id"], "link_url": link["link_url"], "amount": amount}


def get_topup_by_link_id(link_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wallet_topups WHERE link_id = ?", (link_id,)).fetchone()
    return dict(row) if row else None


def list_topups(customer_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_topups WHERE customer_id = ? ORDER BY id DESC", (customer_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_topup_paid(topup_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wallet_topups WHERE id = ?", (topup_id,)).fetchone()
    topup = dict(row)
    if topup["status"] == "PAID":
        return topup

    with get_conn() as conn:
        conn.execute("UPDATE wallet_topups SET status = 'PAID' WHERE id = ?", (topup_id,))
    orders.credit_wallet(topup["customer_id"], topup["amount"])
    log_event("wallet_topup_paid", topup["customer_id"], f"topup={topup_id} amount={topup['amount']}")
    return get_topup_by_link_id(topup["link_id"])


def apply_wallet_to_payment(customer_id: int, pay_now: int) -> int:
    """Returns how much of pay_now the wallet can cover, without debiting yet
    (debiting happens only once the order is actually confirmed)."""
    customer = orders.get_customer(customer_id)
    return min(customer["wallet_balance"], pay_now)
