"""MCP server wrapping Tokri's 6 voice-agent tools, so the Sarvam dashboard
needs exactly ONE tool entry (type: MCP, this server's URL) instead of six
separate API-tool forms.

Runs in its own venv (.venv-mcp) because the `mcp` package pulls a newer
starlette than the main FastAPI app (app/main.py) is pinned to - keeping it
a separate process avoids a dependency conflict. It imports app.services/
app.pipeline directly (no FastAPI/starlette needed for those), so the
business logic lives in exactly one place either way.

Run with: .venv-mcp/bin/python -m app.mcp_server
"""
import os

from mcp.server.fastmcp import FastMCP

from app.db import get_conn, init_db, log_event, seed
from app.pipeline import apply_payment_plan, mark_paid, validate_payment_plan
from app.services import catalog, negotiate, orders, policy

init_db()
seed()

mcp = FastMCP("tokri", stateless_http=True)
# DNS-rebinding protection otherwise rejects every Host header except
# localhost - this server is meant to be reached through a public tunnel
# (set MCP_ALLOWED_HOST to that hostname, e.g. the trycloudflare.com domain).
_allowed_host = os.getenv("MCP_ALLOWED_HOST", "")
if _allowed_host:
    mcp.settings.transport_security.allowed_hosts.append(_allowed_host)
    mcp.settings.transport_security.allowed_origins.append(f"https://{_allowed_host}")


@mcp.tool()
def start_session(phone: str, name: str | None = None, language: str | None = None) -> dict:
    """Call once at the very start of the call to load the customer's profile and catalog."""
    log_event("real_call_tool", None, f"start_session phone={phone!r} name={name!r} language={language!r}")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()

    if row:
        customer = dict(row)
    else:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO customers (phone, name, language) VALUES (?, ?, ?)",
                (phone, name or "Customer", language or "en-IN"),
            )
            customer = orders.get_customer(cur.lastrowid)

    order_id = orders.create_order(customer["id"])
    log_event("real_call_started", customer["id"], f"order={order_id} customer={customer['name']}")
    prof = policy.profile(customer)

    with get_conn() as conn:
        products = [dict(r) for r in conn.execute("SELECT * FROM products WHERE in_stock = 1").fetchall()]
    catalog_payload = [
        {"sku": p["sku"], "name": p["name"], "aliases": catalog.ALIASES.get(p["category"], []), "price": p["price"], "stock": p["in_stock"]}
        for p in products
    ]

    return {
        "order_id": order_id,
        "customer": {
            "id": customer["id"], "name": customer["name"], "tier": prof["tier"],
            "churn_risk": prof["churn_risk"], "credit_available": prof["credit_available"],
            "avg_basket": prof["avg_basket"], "last_order_days": prof["last_order_days"],
            "wallet_balance": customer["wallet_balance"],
        },
        "catalog": catalog_payload,
        "policy": {"max_discount_pct": prof["max_discount_pct"], "prepay_only": prof["prepay_only"], "free_delivery_threshold": policy.FREE_DELIVERY_THRESHOLD},
        "agent_question": prof["agent_question"] if prof["data_status"] == "INCOMPLETE" else None,
    }


@mcp.tool()
def price_basket(order_id: int, items: list[dict]) -> dict:
    """Prices a basket. items: [{"text_or_sku": str, "qty": int|null}, ...]."""
    order = orders.get_order(order_id)
    if not order:
        return {"error": "order not found"}
    customer_id = order["customer_id"]
    log_event("real_call_tool", customer_id, f"price_basket order={order_id} items={items}")

    lines, unmatched, substitutions = [], [], []
    for item in items:
        text_or_sku = item.get("text_or_sku", "")
        qty = item.get("qty")
        product = catalog.get_product(text_or_sku)

        if product and not product["in_stock"]:
            substitute = catalog.find_in_stock_substitute(product)
            if substitute:
                substitutions.append({"requested_sku": product["sku"], "substituted_sku": substitute["sku"], "substituted_name": substitute["name"]})
            product = substitute

        if product:
            q = qty or 1
            matched = {"requested_text": text_or_sku, "matched_sku": product["sku"], "product_name": product["name"], "category": product["category"], "brand": product["brand"], "qty": q, "unit_price": product["price"], "line_total": product["price"] * q}
        else:
            matched = catalog.match_item(customer_id, text_or_sku, qty, None, None)

        if matched["matched_sku"]:
            lines.append(matched)
        else:
            unmatched.append(text_or_sku)

    total = orders.replace_order_items(order_id, lines)
    orders.set_status(order_id, "BASKET_DRAFT")
    updated = orders.get_order(order_id)
    log_event("real_call_basket", customer_id, f"order={order_id} total={total} lines={len(lines)} unmatched={unmatched}")

    return {
        "lines": [{"sku": l["matched_sku"], "name": l["product_name"], "qty": l["qty"], "unit_price": l["unit_price"], "line_total": l["line_total"]} for l in lines],
        "subtotal": updated["subtotal"], "delivery_fee": updated["delivery_fee"], "total": total,
        "unmatched": unmatched, "substitutions": substitutions,
    }


@mcp.tool()
def get_offer(order_id: int, objection: str = "price_high") -> dict:
    """Call when the customer objects to the price. Returns the next valid offer, or cap_reached."""
    order = orders.get_order(order_id)
    if not order:
        return {"error": "order not found"}
    orders.set_status(order_id, "NEGOTIATING")
    offer = negotiate.next_offer(order_id)
    if not offer:
        log_event("real_call_tool", order["customer_id"], f"get_offer order={order_id} -> cap_reached")
        return {"offers": [], "cap_reached": True}
    log_event("real_call_tool", order["customer_id"], f"get_offer order={order_id} -> {offer['description']}")
    return {
        "offers": [{"id": offer["id"], "type": offer["type"], "description": offer["description"], "saving": offer["saving"], "new_total": offer["new_total"]}],
        "cap_reached": False,
    }


@mcp.tool()
def accept_offer(order_id: int, offer_id: str) -> dict:
    """Call when the customer accepts the offer from get_offer."""
    offer = negotiate.next_offer(order_id)
    if not offer or offer["id"] != offer_id:
        return {"error": "offer is no longer valid; call get_offer again"}
    updated = negotiate.accept_offer(order_id, offer)
    log_event("real_call_tool", updated["customer_id"], f"accept_offer order={order_id} new_total={updated['total']}")
    return {"order_id": order_id, "total": updated["total"], "status": updated["status"]}


@mcp.tool()
def set_payment_and_link(order_id: int, pay_now: int) -> dict:
    """Validates pay_now and, if acceptable, creates the Cashfree payment link (or settles instantly from wallet/credit)."""
    order = orders.get_order(order_id)
    if not order:
        return {"error": "order not found"}
    validation = validate_payment_plan(order_id, pay_now)
    if not validation["ok"]:
        log_event("real_call_tool", order["customer_id"], f"set_payment_and_link order={order_id} rejected pay_now={pay_now} min={validation['min_pay_now']}")
        return {**validation, "link_url": None, "status": order["status"]}
    result = apply_payment_plan(order_id, pay_now)
    log_event("real_call_tool", order["customer_id"], f"set_payment_and_link order={order_id} pay_now={pay_now} status={result['status']} link={result.get('link_url')}")
    return {
        "ok": True, "min_pay_now": validation["min_pay_now"], "pay_now": result["pay_now"],
        "outstanding": result["outstanding_new"], "wallet_applied": result["wallet_applied"],
        "link_url": result.get("link_url"), "status": result["status"], "error": None,
    }


@mcp.tool()
def log_outcome(
    order_id: int,
    call_outcome: str,
    final_total: int | None = None,
    payment_choice: str | None = None,
    call_summary: str | None = None,
) -> dict:
    """Call once at call end. call_outcome: LINK_SENT, ON_ACCOUNT, CALLBACK, NO_ORDER, WRONG_PERSON, DND, NO_ANSWER, or VOICEMAIL."""
    order = orders.get_order(order_id)
    if not order:
        return {"error": "order not found"}

    log_event("call_outcome", order["customer_id"], f"order={order_id} outcome={call_outcome} summary={call_summary or ''}")

    terminal = {"NO_ANSWER": "CANCELLED", "VOICEMAIL": "CANCELLED", "DND": "CANCELLED", "WRONG_PERSON": "CANCELLED", "NO_ORDER": "CANCELLED", "CALLBACK": "CANCELLED"}
    status = terminal.get(call_outcome)
    if status and order["status"] not in ("CONFIRMED", "ON_ACCOUNT"):
        orders.set_status(order_id, status)

    return {"order_id": order_id, "logged": True, "order_status": orders.get_order(order_id)["status"]}


if __name__ == "__main__":
    import os

    port = int(os.getenv("MCP_PORT", "8001"))
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
