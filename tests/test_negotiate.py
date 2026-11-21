from app import pipeline
from app.services import negotiate, orders

# A basket where the Farm Select onion (brand-hinted, so it isn't
# auto-substituted for the cheaper FreshPick tier) has a modest Rs.10
# brand-swap saving, and the overall total is large enough that even a New
# customer's 2% cap comfortably covers that Rs.10 - so the swap is actually
# offered rather than skipped as over-cap.
BIG_BASKET_TEXT = "onion farm select, rice, oil, atta, and sugar"


def _basket_order(customer_id: int, text: str = BIG_BASKET_TEXT) -> int:
    call = pipeline.start_call(customer_id)
    pipeline.handle_transcript(call["order_id"], text, "en-IN")
    return call["order_id"]


def test_first_offer_is_a_brand_swap(fresh_db):
    # Anita (customer 7, New) ordered Farm Select onion, which has a cheaper
    # FreshPick equivalent - that's the first rung of the ladder.
    order_id = _basket_order(7)
    offer = negotiate.next_offer(order_id)
    assert offer["type"] == "swap_brand"
    assert offer["saving"] == 10


def test_accepting_offer_lowers_total_and_advances_rung(fresh_db):
    order_id = _basket_order(7)
    order_before = orders.get_order(order_id)
    offer = negotiate.next_offer(order_id)
    updated = negotiate.accept_offer(order_id, offer)

    assert updated["total"] == order_before["total"] - offer["saving"]
    assert updated["offered_rung"] == offer["rung_index"]
    concessions = orders.get_concessions(order_id)
    assert len(concessions) == 1
    assert concessions[0]["offer_type"] == "swap_brand"


def test_cap_reached_eventually_returns_no_offer(fresh_db):
    order_id = _basket_order(7)
    offer = negotiate.next_offer(order_id)
    seen_types = []
    while offer:
        seen_types.append(offer["type"])
        negotiate.accept_offer(order_id, offer)
        offer = negotiate.next_offer(order_id)
    assert offer is None
    assert "swap_brand" in seen_types


def test_gold_customer_keeps_more_room_after_the_same_swap(fresh_db):
    # Same basket, same first swap saving for both - but Sunita's (customer
    # 1) Gold 5% cap leaves far more room afterwards than Anita's New 2%.
    gold_order = _basket_order(1)
    new_order = _basket_order(7)

    gold_offer = negotiate.next_offer(gold_order)
    new_offer = negotiate.next_offer(new_order)
    assert gold_offer["saving"] == new_offer["saving"] == 10
    negotiate.accept_offer(gold_order, gold_offer)
    negotiate.accept_offer(new_order, new_offer)

    gold_next = negotiate.next_offer(gold_order)
    new_next = negotiate.next_offer(new_order)
    assert gold_next["type"] == "loyalty_discount"
    assert new_next["type"] == "loyalty_discount"
    assert gold_next["saving"] > new_next["saving"]


def test_bundle_offer_uses_the_real_bundle_price(fresh_db):
    # 2x Fortune Basmati Rice (RIC-03): bundle_price_2 is 178, vs 2*92=184,
    # a Rs.6 saving - straight from the inventory sheet, not a flat guess.
    # The rest of the basket just pushes the subtotal past the free-delivery
    # threshold so that rung isn't offered first instead.
    call = pipeline.start_call(1)
    pipeline.handle_transcript(call["order_id"], "2 rice, atta, oil, and sugar", "en-IN")
    offer = negotiate.next_offer(call["order_id"])
    assert offer["type"] == "bundle_discount"
    assert offer["saving"] == 6
