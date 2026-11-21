"""The concession ladder: given a basket and a price objection, returns the
next valid offer, one at a time, never below a SKU's floor price and never
past the customer's discount cap. The voice agent may only ever say a
concession this module actually returned - it must not invent a discount.
"""
from app.db import get_conn
from app.services import catalog, policy

# Cheapest, most generically useful item to suggest adding to cross the
# free-delivery threshold (iodised salt, Rs.22).
FREE_DELIVERY_FILLER_SKU = "SLT-03"


def _max_discount_amount(original_total: int, cap_pct: float) -> int:
    return int(original_total * cap_pct / 100)


def _rung_swap_cheaper_brand(order_items: list[dict], remaining_room: int) -> dict | None:
    best = None
    for item in order_items:
        product = catalog.get_product(item["matched_sku"])
        if not product or not product["alt_sku"]:
            continue
        alt = catalog.get_product(product["alt_sku"])
        if not alt:
            continue
        saving = (item["unit_price"] - alt["price"]) * item["qty"]
        if saving <= 0 or saving > remaining_room:
            continue
        if best is None or saving > best["saving"]:
            best = {
                "type": "swap_brand",
                "description": f"Swap {product['name']} for {alt['name']} and save Rs.{saving}",
                "saving": saving,
                "item_id": item["id"],
                "new_sku": alt["sku"],
                "new_unit_price": alt["price"],
            }
    return best


def _rung_free_delivery_filler(order: dict, remaining_room: int) -> dict | None:
    if order["subtotal"] >= policy.FREE_DELIVERY_THRESHOLD or order["delivery_fee"] <= 0:
        return None
    filler = catalog.get_product(FREE_DELIVERY_FILLER_SKU)
    if not filler:
        return None
    saving = order["delivery_fee"] - filler["price"]
    if saving <= 0 or saving > remaining_room:
        return None
    return {
        "type": "add_for_free_delivery",
        "description": f"Add {filler['name']} (Rs.{filler['price']}) to cross Rs.{policy.FREE_DELIVERY_THRESHOLD} and drop the Rs.{order['delivery_fee']} delivery fee",
        "saving": saving,
        "add_sku": filler["sku"],
        "add_price": filler["price"],
    }


def _rung_bundle_repeated_item(order_items: list[dict], remaining_room: int) -> dict | None:
    for item in order_items:
        if item["qty"] < 2 or not item["matched_sku"]:
            continue
        product = catalog.get_product(item["matched_sku"])
        if not product or not product["bundle_price_2"]:
            continue
        saving = 2 * product["price"] - product["bundle_price_2"]
        if saving <= 0 or saving > remaining_room:
            continue
        return {
            "type": "bundle_discount",
            "description": f"Bundle price on {item['qty']}x {product['name']}: 2 for Rs.{product['bundle_price_2']} (save Rs.{saving})",
            "saving": saving,
            "item_id": item["id"],
        }
    return None


def _rung_loyalty_discount(remaining_room: int, cap_pct: float) -> dict | None:
    if remaining_room <= 0:
        return None
    return {
        "type": "loyalty_discount",
        "description": f"A loyalty discount of Rs.{remaining_room} (up to your {cap_pct:g}% limit)",
        "saving": remaining_room,
    }


RUNGS = ["swap_brand", "add_for_free_delivery", "bundle_discount", "loyalty_discount"]


def next_offer(order_id: int) -> dict | None:
    """Returns the next valid offer for this order, respecting which rungs
    have already been offered (order.offered_rung) so the agent never repeats
    or skips the ladder order, and respecting the remaining discount room.
    """
    from app.services import orders as orders_service

    order = orders_service.get_order(order_id)
    customer = orders_service.get_customer(order["customer_id"])
    order_items = orders_service.get_order_items(order_id)

    prof = policy.profile(customer)
    original_total = order["subtotal"] + order["delivery_fee"]
    max_discount = _max_discount_amount(original_total, prof["max_discount_pct"])
    remaining_room = max_discount - order["discount_total"]

    rung_index = order["offered_rung"]
    while rung_index < len(RUNGS):
        rung = RUNGS[rung_index]
        offer = None
        if rung == "swap_brand":
            offer = _rung_swap_cheaper_brand(order_items, remaining_room)
        elif rung == "add_for_free_delivery":
            offer = _rung_free_delivery_filler(order, remaining_room)
        elif rung == "bundle_discount":
            offer = _rung_bundle_repeated_item(order_items, remaining_room)
        elif rung == "loyalty_discount":
            offer = _rung_loyalty_discount(remaining_room, prof["max_discount_pct"])

        rung_index += 1
        if offer:
            offer["id"] = f"{order_id}-{rung}"
            offer["rung_index"] = rung_index
            offer["new_total"] = original_total - order["discount_total"] - offer["saving"]
            return offer

    return None


def accept_offer(order_id: int, offer: dict) -> dict:
    """Applies the accepted offer: updates order_items/discount_total/total
    and logs a concessions row. Returns the updated order.
    """
    from app.db import now
    from app.services import orders as orders_service

    order = orders_service.get_order(order_id)

    if offer["type"] == "swap_brand":
        new_brand = catalog.get_product(offer["new_sku"])["brand"]
        orders_service.update_order_item_sku(offer["item_id"], offer["new_sku"], offer["new_unit_price"], new_brand)
        new_subtotal = orders_service.recompute_subtotal(order_id)
        orders_service.set_pricing(order_id, subtotal=new_subtotal, discount_total=order["discount_total"])
    elif offer["type"] == "add_for_free_delivery":
        orders_service.add_order_item(
            order_id,
            requested_text=offer["add_sku"],
            matched_sku=offer["add_sku"],
            category=catalog.get_product(offer["add_sku"])["category"],
            brand=catalog.get_product(offer["add_sku"])["brand"],
            qty=1,
            unit_price=offer["add_price"],
        )
        new_subtotal = orders_service.recompute_subtotal(order_id)
        orders_service.set_pricing(order_id, subtotal=new_subtotal, delivery_fee=0, discount_total=order["discount_total"])
    else:
        new_discount_total = order["discount_total"] + offer["saving"]
        orders_service.set_pricing(order_id, discount_total=new_discount_total)

    orders_service.bump_offered_rung(order_id, offer["rung_index"])
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO concessions (order_id, offer_type, description, saving, created_at) VALUES (?, ?, ?, ?, ?)",
            (order_id, offer["type"], offer["description"], offer["saving"], now()),
        )

    return orders_service.get_order(order_id)
