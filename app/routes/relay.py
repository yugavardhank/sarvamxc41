"""The tool-facing contract: the public HTTP surface Sarvam's own
prompt-driven agent (the "Sales Discovery"-style template, configured with
On-start/During-call/On-end HTTP tools - not a Cashfree Relay canvas/router)
calls to run a Tokri call. The agent's LLM does the talking and turn-by-turn
extraction; every number, price, discount and credit decision is computed
here, and the agent may only ever say what these endpoints return.

Suggested tool wiring (keep the tool count low - every call adds latency
on a live voice call):
  - On start:     start_session      -> POST /session/start
  - During call:  price_basket       -> POST /basket/price      (after each batch of items/corrections)
  - During call:  get_offer          -> POST /negotiate          (on a price objection; returns cap_reached when nothing's left)
  - During call:  accept_offer       -> POST /offer/accept
  - During call:  set_payment_and_link -> POST /payment/plan-and-link (merges plan + link into one call)
  - On end:       log_outcome        -> POST /call/outcome

Endpoint shapes originally followed the "Bhaav" negotiation-agent contract;
granular /payment/plan and /payment/link are kept too for direct testing,
but the voice tool should call the combined /payment/plan-and-link.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_conn, log_event, now
from app.pipeline import apply_payment_plan, mark_paid, min_pay_now_for, validate_payment_plan
from app.services import catalog, negotiate, orders, policy

router = APIRouter()


class SessionStartRequest(BaseModel):
    phone: str
    name: str | None = None
    language: str | None = None


@router.post("/session/start")
def session_start(body: SessionStartRequest):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (body.phone,)).fetchone()

    if row:
        customer = dict(row)
    else:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO customers (phone, name, language) VALUES (?, ?, ?)",
                (body.phone, body.name or "Customer", body.language or "en-IN"),
            )
            customer = orders.get_customer(cur.lastrowid)

    order_id = orders.create_order(customer["id"])
    prof = policy.profile(customer)

    with get_conn() as conn:
        products = [dict(r) for r in conn.execute("SELECT * FROM products WHERE in_stock = 1").fetchall()]
    catalog_payload = [
        {
            "sku": p["sku"],
            "name": p["name"],
            "aliases": catalog.ALIASES.get(p["category"], []),
            "unit": "",
            "price": p["price"],
            "stock": p["in_stock"],
        }
        for p in products
    ]

    return {
        "order_id": order_id,
        "customer": {
            "id": customer["id"],
            "name": customer["name"],
            "tier": prof["tier"],
            "churn_risk": prof["churn_risk"],
            "credit_available": prof["credit_available"],
            "avg_basket": prof["avg_basket"],
            "on_time_rate": customer["on_time_rate"],
            "last_order_days": prof["last_order_days"],
            "wallet_balance": customer["wallet_balance"],
        },
        "catalog": catalog_payload,
        "policy": {
            "max_discount_pct": prof["max_discount_pct"],
            "prepay_only": prof["prepay_only"],
            "free_delivery_threshold": policy.FREE_DELIVERY_THRESHOLD,
        },
        # If not empty, the agent must ask this before pricing anything -
        # see tokri_Database.xlsx's data_status/missing_fields columns.
        "agent_question": prof["agent_question"] if prof["data_status"] == "INCOMPLETE" else None,
        "data_status": prof["data_status"],
    }


class BasketItem(BaseModel):
    text_or_sku: str
    qty: int | None = None


class BasketPriceRequest(BaseModel):
    order_id: int
    items: list[BasketItem]


@router.post("/basket/price")
def basket_price(body: BasketPriceRequest):
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    customer_id = order["customer_id"]

    lines = []
    unmatched = []
    substitutions = []
    for item in body.items:
        product = catalog.get_product(item.text_or_sku)  # exact-SKU lookup, any stock status

        if product and not product["in_stock"]:
            substitute = catalog.find_in_stock_substitute(product)
            if substitute:
                substitutions.append({"requested_sku": product["sku"], "substituted_sku": substitute["sku"], "substituted_name": substitute["name"]})
            product = substitute

        if product:
            qty = item.qty or 1
            matched = {
                "requested_text": item.text_or_sku, "matched_sku": product["sku"],
                "product_name": product["name"], "category": product["category"],
                "brand": product["brand"], "qty": qty, "unit_price": product["price"],
                "line_total": product["price"] * qty,
            }
        else:
            matched = catalog.match_item(customer_id, item.text_or_sku, item.qty, None, None)

        if matched["matched_sku"]:
            lines.append(matched)
        else:
            unmatched.append(item.text_or_sku)

    total = orders.replace_order_items(body.order_id, lines)
    orders.set_status(body.order_id, "BASKET_DRAFT")
    updated = orders.get_order(body.order_id)

    return {
        "lines": [
            {
                "sku": l["matched_sku"], "name": l["product_name"], "qty": l["qty"],
                "unit_price": l["unit_price"], "line_total": l["line_total"], "in_stock": True,
            }
            for l in lines
        ],
        "subtotal": updated["subtotal"], "delivery_fee": updated["delivery_fee"],
        "total": total, "unmatched": unmatched, "substitutions": substitutions,
    }


class NegotiateRequest(BaseModel):
    order_id: int
    objection: str = "price_high"


@router.post("/negotiate")
def negotiate_route(body: NegotiateRequest):
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    orders.set_status(body.order_id, "NEGOTIATING")
    offer = negotiate.next_offer(body.order_id)
    if not offer:
        return {"offers": [], "cap_reached": True}
    return {
        "offers": [{"id": offer["id"], "type": offer["type"], "description": offer["description"], "saving": offer["saving"], "new_total": offer["new_total"]}],
        "cap_reached": False,
    }


class OfferAcceptRequest(BaseModel):
    order_id: int
    offer_id: str


@router.post("/offer/accept")
def offer_accept(body: OfferAcceptRequest):
    offer = negotiate.next_offer(body.order_id)
    if not offer or offer["id"] != body.offer_id:
        raise HTTPException(status_code=409, detail="offer is no longer valid; call /negotiate again")
    updated = negotiate.accept_offer(body.order_id, offer)
    return {"order_id": body.order_id, "total": updated["total"], "status": updated["status"]}


class PaymentPlanRequest(BaseModel):
    order_id: int
    pay_now: int


@router.post("/payment/plan")
def payment_plan(body: PaymentPlanRequest):
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    result = validate_payment_plan(body.order_id, body.pay_now)
    if result["ok"]:
        orders.set_payment_plan(body.order_id, body.pay_now, result["outstanding"], 0)
    return result


class PaymentLinkRequest(BaseModel):
    order_id: int


@router.post("/payment/link")
def payment_link(body: PaymentLinkRequest):
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if order["pay_now"] is None:
        raise HTTPException(status_code=409, detail="call /payment/plan first")
    result = apply_payment_plan(body.order_id, order["pay_now"])
    return {
        "link_id": orders.get_order(body.order_id)["link_id"],
        "link_url": result.get("link_url"),
        "status": result["status"],
    }


class PlanAndLinkRequest(BaseModel):
    order_id: int
    pay_now: int


@router.post("/payment/plan-and-link")
def payment_plan_and_link(body: PlanAndLinkRequest):
    """The single tool call the voice agent should use: validates pay_now,
    and only if valid does it touch the wallet/ledger or create a link.
    On rejection it returns the same shape with ok=False and min_pay_now,
    so the agent can state the minimum once and ask again.
    """
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    validation = validate_payment_plan(body.order_id, body.pay_now)
    if not validation["ok"]:
        return {**validation, "link_url": None, "status": order["status"]}

    result = apply_payment_plan(body.order_id, body.pay_now)
    return {
        "ok": True, "min_pay_now": validation["min_pay_now"], "pay_now": result["pay_now"],
        "outstanding": result["outstanding_new"], "wallet_applied": result["wallet_applied"],
        "link_url": result.get("link_url"), "status": result["status"], "error": None,
    }


class OrderConfirmRequest(BaseModel):
    order_id: int
    link_id: str | None = None
    status: str | None = None


@router.post("/order/confirm")
def order_confirm(body: OrderConfirmRequest):
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    result = mark_paid(body.order_id)
    return {"order_id": body.order_id, **result}


CALL_OUTCOME_TERMINAL_STATUSES = {
    "NO_ANSWER": "CANCELLED", "VOICEMAIL": "CANCELLED", "DND": "CANCELLED",
    "WRONG_PERSON": "CANCELLED", "NO_ORDER": "CANCELLED", "CALLBACK": "CANCELLED",
}


class CallOutcomeRequest(BaseModel):
    order_id: int
    call_outcome: str  # LINK_SENT, ON_ACCOUNT, CALLBACK, NO_ORDER, WRONG_PERSON, DND, NO_ANSWER, VOICEMAIL
    order_items: list | None = None
    budget: float | None = None
    objection_raised: bool | None = None
    accepted_offer_ids: list[str] | None = None
    final_total: int | None = None
    payment_choice: str | None = None
    pay_now_amount: float | None = None
    callback_requested: bool | None = None
    alt_contact_number: str | None = None
    language_used: str | None = None
    call_summary: str | None = None


@router.post("/call/outcome")
def call_outcome(body: CallOutcomeRequest):
    """The On-end hook: logs the call's output variables and, for a terminal
    non-order outcome (no answer, DND, wrong person, declined, callback),
    cancels the draft order instead of leaving it dangling in BASKET_DRAFT.
    """
    order = orders.get_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")

    log_event(
        "call_outcome", order["customer_id"],
        f"order={body.order_id} outcome={body.call_outcome} summary={body.call_summary or ''}",
    )

    terminal_status = CALL_OUTCOME_TERMINAL_STATUSES.get(body.call_outcome)
    if terminal_status and order["status"] not in ("CONFIRMED", "ON_ACCOUNT"):
        orders.set_status(body.order_id, terminal_status)

    return {"order_id": body.order_id, "logged": True, "order_status": orders.get_order(body.order_id)["status"]}


@router.get("/api/customer/{customer_id}")
def get_customer_profile(customer_id: int):
    customer = orders.get_customer(customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return {**customer, "profile": policy.profile(customer)}
