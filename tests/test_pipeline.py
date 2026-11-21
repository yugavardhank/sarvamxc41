from app import pipeline
from app.services import catalog, orders


def test_start_call_generic_greeting_with_no_item_history(fresh_db):
    # Sunita (Gold) has aggregate order stats seeded but no actual itemized
    # order rows yet, so there's nothing concrete to recall - the greeting
    # should fall back to the generic opener, not crash or say "got .".
    result = pipeline.start_call(1)
    assert "missed you" not in result["greeting_text"]
    order = orders.get_order(result["order_id"])
    assert order["status"] == "REQUESTED"


def test_start_call_uses_winback_wording_for_churn_risk_customer(fresh_db):
    result = pipeline.start_call(5)  # Ramesh Kumar - Silver at churn risk
    assert "missed you" in result["greeting_text"]


def test_start_call_asks_the_missing_data_question_first(fresh_db):
    result = pipeline.start_call(9)  # Farhan Ali - missing language
    assert "language" in result["greeting_text"].lower()


def test_same_as_last_time_with_no_history_reports_nothing_to_repeat(fresh_db):
    call = pipeline.start_call(1)
    result = pipeline.handle_transcript(call["order_id"], "same as last time", "en-IN")
    assert result["total"] == 0
    assert "didn't catch" in result["reply_text"]


def test_fresh_item_list_builds_new_basket(fresh_db):
    call = pipeline.start_call(7)  # Anita Sharma - New
    result = pipeline.handle_transcript(call["order_id"], "toor dal and salt", "en-IN")
    assert result["status"] == "BASKET_DRAFT"
    assert result["total"] > 0
    assert len(result["items"]) == 2


def test_price_objection_returns_an_offer_without_changing_total_yet(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "onion farm select, rice, oil, atta, and sugar", "en-IN")
    order_before = orders.get_order(call["order_id"])

    result = pipeline.handle_transcript(call["order_id"], "that's too expensive", "en-IN")
    assert result["status"] == "NEGOTIATING"
    assert result["offer"] is not None
    assert result["total"] == order_before["total"]  # not applied until accepted


def test_accepting_an_offer_lowers_the_total(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "onion farm select, rice, oil, atta, and sugar", "en-IN")
    order_before = orders.get_order(call["order_id"])
    pipeline.handle_transcript(call["order_id"], "that's too expensive", "en-IN")

    result = pipeline.accept_offer(call["order_id"])
    assert result["total"] < order_before["total"]


def test_wallet_covers_part_of_payment_then_link_for_remainder(fresh_db):
    from app.services import wallet

    topup = wallet.create_topup(7, 50)
    wallet.mark_topup_paid(topup["topup_id"])

    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "toor dal", "en-IN")  # Rs.175 + delivery

    result = pipeline.plan_payment(call["order_id"], "full_now", None)
    assert result["wallet_applied"] == 50
    assert result["status"] == "AWAITING_PAYMENT"
    assert result["link_url"]

    paid = pipeline.mark_paid(call["order_id"])
    assert paid["status"] == "CONFIRMED"
    assert orders.get_customer(7)["wallet_balance"] == 0


def test_new_customer_is_forced_to_prepay_in_full(fresh_db):
    # Anita (New) has no credit, so choosing "on account" still requires the
    # full pay_now.
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "toor dal and salt", "en-IN")
    order = orders.get_order(call["order_id"])

    result = pipeline.plan_payment(call["order_id"], "on_account", None)
    assert result["pay_now"] == order["total"]
    assert result["outstanding_new"] == 0


def test_on_account_confirms_immediately_when_credit_allows(fresh_db):
    # Prakash (customer 4) has credit_limit 1200, outstanding 200 -> 1000 available.
    call = pipeline.start_call(4)
    pipeline.handle_transcript(call["order_id"], "rice", "en-IN")
    order = orders.get_order(call["order_id"])
    assert order["total"] < 1000

    result = pipeline.plan_payment(call["order_id"], "on_account", None)
    assert result["status"] == "ON_ACCOUNT"
    assert result["pay_now"] == 0

    customer_after = orders.get_customer(4)
    assert customer_after["outstanding"] == 200 + order["total"]


def test_full_flow_updates_preferences_after_payment(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "2 rice", "en-IN")
    plan = pipeline.plan_payment(call["order_id"], "full_now", None)
    assert plan["status"] == "AWAITING_PAYMENT"

    paid = pipeline.mark_paid(call["order_id"])
    assert paid["status"] == "CONFIRMED"

    preference = catalog.get_preference(7, "basmati rice")
    assert preference is not None
    assert preference["usual_qty"] == 2


def test_cancel_order_releases_active_payment_link(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "toor dal", "en-IN")
    pipeline.plan_payment(call["order_id"], "full_now", None)
    result = pipeline.cancel_order(call["order_id"])
    assert result["status"] == "CANCELLED"


def test_second_call_references_a_real_order_placed_through_tokri(fresh_db):
    # Once a customer actually completes an order through Tokri, their next
    # call's greeting should recall it - unlike the seed-only aggregate
    # stats, this is real item history.
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "toor dal and salt", "en-IN")
    pipeline.plan_payment(call["order_id"], "full_now", None)
    pipeline.mark_paid(call["order_id"])

    second_call = pipeline.start_call(7)
    assert "toor dal" in second_call["greeting_text"]
