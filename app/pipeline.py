from app.db import log_event
from app.services import cashfree, catalog, lists, negotiate, orders, policy, sarvam, wallet


def start_call(customer_id: int) -> dict:
    """Begins a reorder call: creates an order and builds the greeting,
    referencing the customer's last confirmed order and, for a churn-risk
    customer, offering a win-back note instead of the plain greeting.
    """
    customer = orders.get_customer(customer_id)
    prof = policy.profile(customer)
    last_order = orders.get_last_confirmed_order(customer_id)

    summary = None
    if last_order:
        items = orders.get_order_items(last_order["id"])
        summary = ", ".join(f"{item['qty']} {item['category']}" for item in items if item["category"])

    if prof["data_status"] == "INCOMPLETE" and prof.get("agent_question"):
        # Missing data (e.g. no language on file) - ask before anything else,
        # rather than guessing. Policy already falls back to New/prepay-only
        # until this is answered.
        greeting = f"Hi {customer['name']}, this is Tokri calling. {prof['agent_question']}"
    elif prof["churn_risk"]:
        # Win-back wording applies whenever the signals say so, whether or
        # not we have the specific items from their last order on file.
        recall = f" Last time you got {summary}." if summary else ""
        greeting = (
            f"Hi {customer['name']}, this is Tokri - we've missed you!{recall} Want your "
            "usual again, something different, or should I check if there's a special "
            "offer for you today?"
        )
    elif summary:
        greeting = (
            f"Hi {customer['name']}, this is Tokri calling for your usual reorder. "
            f"Last time you got {summary}. Same again, or anything different today?"
        )
    else:
        greeting = (
            f"Hi {customer['name']}, this is Tokri calling to help you reorder your "
            "groceries. What would you like today?"
        )

    order_id = orders.create_order(customer_id)
    orders.touch_last_called(customer_id)
    log_event("call_started", customer_id, f"order={order_id}")

    return {
        "order_id": order_id,
        "language_code": customer["language"] or "en-IN",
        "greeting_text": greeting,
        "profile": prof,
    }


def _cancel_active_link_if_any(order: dict) -> None:
    if order["status"] == "AWAITING_PAYMENT" and order["link_id"]:
        cashfree.cancel_payment_link(order["link_id"])
        orders.set_payment_link(order["id"], None, None)


def _reply_for_basket(matched: list[dict], total: int, customer_id: int) -> str:
    unmatched = [m for m in matched if not m["matched_sku"]]
    matched_lines = [
        f"{item['qty']} {item['product_name'] or item['requested_text']} for Rs.{item['line_total']}"
        for item in matched
        if item["matched_sku"]
    ]

    if not matched:
        return "I didn't catch any items. Could you tell me what you'd like to order?"
    if not matched_lines:
        missing = ", ".join(m["requested_text"] for m in unmatched)
        return f"I couldn't find {missing} in our catalog. Could you try another item?"

    reply = "Your basket: " + ", ".join(matched_lines) + f". Total is Rs.{total}. Shall I confirm, or is the price too high?"
    if unmatched:
        missing = ", ".join(m["requested_text"] for m in unmatched)
        reply += f" I couldn't find: {missing}."

    customer = orders.get_customer(customer_id)
    if customer["wallet_balance"] > 0:
        reply += f" You have Rs.{customer['wallet_balance']} in your Tokri wallet, which I'll use first."
    return reply


def handle_transcript(order_id: int, transcript: str, language_code: str) -> dict:
    """Runs understand -> match -> price for a customer's spoken turn.

    Handles, in priority order: saving the current basket as a named list,
    a price objection (routes to the negotiation ladder), loading a named
    saved list, "same as last time", or a fresh free-form item list.

    Returns {"status", "items", "total", "reply_text", "offer"(optional)}.
    """
    order = orders.get_order(order_id)
    customer_id = order["customer_id"]
    log_event("transcript", customer_id, f"order={order_id} text={transcript}")

    list_save_name = sarvam.extract_list_save_name(transcript)
    if list_save_name:
        items = orders.get_order_items(order_id)
        if not items:
            reply = "You don't have a basket yet to save as a list."
        else:
            lists.save_list(customer_id, list_save_name, items)
            reply = f"Saved as your '{list_save_name}' list - just say 'order from my {list_save_name} list' next time."
        return {"status": order["status"], "items": items, "total": order["total"], "reply_text": reply}

    if sarvam.wants_price_objection(transcript):
        offer = negotiate.next_offer(order_id)
        orders.set_status(order_id, "NEGOTIATING")
        if offer:
            reply = f"{offer['description']}. That brings your total to Rs.{offer['new_total']}. Shall I apply this?"
        else:
            reply = "I'm sorry, I can't bring the price down any further on this order. I can offer a callback, or we continue at the current price."
        return {
            "status": "NEGOTIATING",
            "items": orders.get_order_items(order_id),
            "total": order["total"],
            "reply_text": reply,
            "offer": offer,
        }

    _cancel_active_link_if_any(order)

    list_ref_name = sarvam.extract_list_reference_name(transcript)
    if list_ref_name:
        saved = lists.find_list_by_name(customer_id, list_ref_name)
        if not saved:
            return {
                "status": order["status"],
                "items": orders.get_order_items(order_id),
                "total": order["total"],
                "reply_text": f"I couldn't find a list called '{list_ref_name}'. Would you like to tell me the items instead?",
            }
        list_items = lists.get_list_items(saved["id"])
        matched = [_matched_from_saved(item) for item in list_items]
    elif sarvam.wants_same_as_last_time(transcript):
        last_order = orders.get_last_confirmed_order(customer_id)
        matched = (
            [_matched_from_saved(item) for item in orders.get_order_items(last_order["id"])]
            if last_order
            else []
        )
    else:
        extraction = sarvam.extract_items(transcript, language_code)
        matched = catalog.build_basket(customer_id, extraction["items"], extraction.get("budget_hint"))

    total = orders.replace_order_items(order_id, matched)
    orders.set_status(order_id, "BASKET_DRAFT")
    log_event("basket_built", customer_id, f"order={order_id} total={total} items={len(matched)}")

    return {
        "status": "BASKET_DRAFT",
        "items": matched,
        "total": total,
        "reply_text": _reply_for_basket(matched, total, customer_id),
    }


def _matched_from_saved(item: dict) -> dict:
    product = catalog.get_product(item["matched_sku"])
    return {
        "requested_text": item["category"] or item["requested_text"],
        "matched_sku": item["matched_sku"],
        "product_name": product["name"] if product else None,
        "category": item["category"],
        "brand": item.get("brand"),
        "qty": item["qty"],
        "unit_price": product["price"] if product else 0,
        "line_total": (product["price"] if product else 0) * item["qty"],
    }


def accept_offer(order_id: int) -> dict:
    """Customer accepted the most recently offered concession."""
    offer = negotiate.next_offer(order_id)
    order = orders.get_order(order_id)
    # next_offer is idempotent against offered_rung, but accept_offer needs
    # the *same* offer just shown, not a fresh lookup after it may have
    # changed state - callers should pass the offer they displayed. For the
    # simple voice flow (one offer on screen at a time) this is safe because
    # nothing else mutates discount_total between showing and accepting.
    if not offer:
        return {"status": order["status"], "reply_text": "There's no pending offer to accept."}

    updated = negotiate.accept_offer(order_id, offer)
    reply = f"Done - {offer['description']}. Your new total is Rs.{updated['total']}."
    return {
        "status": updated["status"],
        "total": updated["total"],
        "items": orders.get_order_items(order_id),
        "reply_text": reply,
    }


def decline_further_negotiation(order_id: int) -> dict:
    orders.set_status(order_id, "BASKET_DRAFT")
    order = orders.get_order(order_id)
    return {"status": "BASKET_DRAFT", "reply_text": f"No problem, your total stays at Rs.{order['total']}."}


def min_pay_now_for(order_id: int) -> int:
    order = orders.get_order(order_id)
    customer = orders.get_customer(order["customer_id"])
    credit_avail = policy.profile(customer)["credit_available"]
    return max(0, order["total"] - credit_avail)


def validate_payment_plan(order_id: int, pay_now: int) -> dict:
    """Read-only check: is this pay_now acceptable given the customer's
    credit? Mirrors the Relay contract's /payment/plan, which only validates
    - it does not create a link or touch the wallet/ledger.
    """
    order = orders.get_order(order_id)
    min_pay_now = min_pay_now_for(order_id)
    if pay_now < min_pay_now:
        return {
            "ok": False, "min_pay_now": min_pay_now, "pay_now": pay_now,
            "outstanding": order["total"] - pay_now,
            "error": f"The minimum I can accept now is Rs.{min_pay_now}.",
        }
    return {"ok": True, "min_pay_now": min_pay_now, "pay_now": pay_now, "outstanding": order["total"] - pay_now, "error": None}


def apply_payment_plan(order_id: int, pay_now: int) -> dict:
    """Commits an already-validated pay_now: applies the wallet first, then
    either confirms immediately (wallet covered it all, or nothing is owed
    now) or creates a Cashfree link for the remainder.
    """
    order = orders.get_order(order_id)
    customer = orders.get_customer(order["customer_id"])
    total = order["total"]

    outstanding_new = total - pay_now
    wallet_applied = wallet.apply_wallet_to_payment(order["customer_id"], pay_now)
    link_amount = pay_now - wallet_applied

    orders.set_payment_plan(order_id, pay_now, outstanding_new, wallet_applied)

    if wallet_applied > 0:
        orders.debit_wallet(order["customer_id"], wallet_applied)

    if link_amount <= 0:
        if outstanding_new > 0:
            orders.add_outstanding(order["customer_id"], outstanding_new)
        _finalize_order(order_id, confirmed_now=pay_now > 0)
        status = orders.get_order(order_id)["status"]
        reply = _payment_confirmed_reply(pay_now, wallet_applied, outstanding_new, status)
        return {
            "status": status, "pay_now": pay_now, "wallet_applied": wallet_applied,
            "outstanding_new": outstanding_new, "link_url": None, "reply_text": reply,
        }

    link = cashfree.create_payment_link(
        amount=link_amount,
        customer_phone=customer["phone"],
        purpose=f"Tokri order #{order_id}",
        order_id=order_id,
        customer_name=customer["name"],
    )
    orders.set_payment_link(order_id, link["link_id"], link["link_url"])
    orders.set_status(order_id, "AWAITING_PAYMENT")
    log_event("order_awaiting_payment", order["customer_id"], f"order={order_id} link={link['link_id']} amount={link_amount}")

    reply = f"I've sent a payment link for Rs.{link_amount}."
    if wallet_applied > 0:
        reply += f" Rs.{wallet_applied} from your wallet already covered part of it."
    if outstanding_new > 0:
        reply += f" Rs.{outstanding_new} will be on your account."
    reply += " Your order is placed once it's paid."

    return {
        "status": "AWAITING_PAYMENT", "pay_now": pay_now, "wallet_applied": wallet_applied,
        "outstanding_new": outstanding_new, "link_url": link["link_url"], "reply_text": reply,
    }


def plan_payment(order_id: int, payment_choice: str | None, pay_now_amount: float | None) -> dict:
    """Convenience wrapper for the voice flow: derives a proposed pay_now
    from the customer's spoken payment choice, then validates and applies it
    in one turn (the Relay contract instead exposes validate/apply as two
    separate endpoints so Relay can re-prompt on rejection first).
    """
    order = orders.get_order(order_id)
    total = order["total"]

    if payment_choice == "on_account":
        proposed = 0
    elif payment_choice == "part_now":
        proposed = int(pay_now_amount) if pay_now_amount else total // 2
    else:  # full_now or unspecified
        proposed = total

    min_pay_now = min_pay_now_for(order_id)
    pay_now = max(proposed, min_pay_now)
    return apply_payment_plan(order_id, pay_now)


def _payment_confirmed_reply(pay_now: int, wallet_applied: int, outstanding_new: int, status: str) -> str:
    if status == "ON_ACCOUNT":
        return f"No payment needed right now - Rs.{outstanding_new} has been added to your account. Your order is placed."
    reply = "Payment received"
    if wallet_applied > 0:
        reply += f" from your Tokri wallet (Rs.{wallet_applied})"
    reply += ". Your order is placed."
    if outstanding_new > 0:
        reply += f" Rs.{outstanding_new} remains on your account."
    return reply


def cancel_order(order_id: int) -> dict:
    order = orders.get_order(order_id)
    _cancel_active_link_if_any(order)
    orders.set_status(order_id, "CANCELLED")
    return {"status": "CANCELLED", "reply_text": "Okay, I've cancelled this order."}


def mark_paid(order_id: int) -> dict:
    """A Cashfree payment event came in for this order's link: confirms it
    and learns brand/quantity preferences from the accepted basket.
    """
    order = orders.get_order(order_id)
    if order["status"] in ("CONFIRMED", "ON_ACCOUNT"):
        return {"status": order["status"], "reply_text": "This order was already paid."}

    if order["outstanding_new"]:
        orders.add_outstanding(order["customer_id"], order["outstanding_new"])

    _finalize_order(order_id, confirmed_now=True)
    return {"status": orders.get_order(order_id)["status"], "reply_text": "Payment received, your order is placed."}


def _finalize_order(order_id: int, confirmed_now: bool) -> None:
    order = orders.get_order(order_id)
    items = orders.get_order_items(order_id)
    for item in items:
        if item["matched_sku"] and item["category"]:
            product = catalog.get_product(item["matched_sku"])
            catalog.upsert_preference(order["customer_id"], item["category"], product["brand"] if product else None, item["qty"])

    orders.set_status(order_id, "CONFIRMED" if confirmed_now else "ON_ACCOUNT")
    log_event("order_paid", order["customer_id"], f"order={order_id} total={order['total']}")
