from app.services import orders, wallet


def test_topup_credits_wallet_once_paid(fresh_db):
    before = orders.get_customer(7)["wallet_balance"]
    topup = wallet.create_topup(7, 300)
    assert orders.get_customer(7)["wallet_balance"] == before  # not credited yet

    wallet.mark_topup_paid(topup["topup_id"])
    assert orders.get_customer(7)["wallet_balance"] == before + 300


def test_marking_paid_twice_is_a_no_op(fresh_db):
    topup = wallet.create_topup(7, 300)
    wallet.mark_topup_paid(topup["topup_id"])
    balance_after_first = orders.get_customer(7)["wallet_balance"]
    wallet.mark_topup_paid(topup["topup_id"])
    assert orders.get_customer(7)["wallet_balance"] == balance_after_first


def test_wallet_covers_part_of_a_payment(fresh_db):
    wallet.create_topup(1, 150)
    topups = wallet.list_topups(1)
    wallet.mark_topup_paid(topups[0]["id"])

    covered = wallet.apply_wallet_to_payment(1, 221)
    assert covered == 150


def test_wallet_covers_all_when_sufficient(fresh_db):
    wallet.create_topup(1, 500)
    topups = wallet.list_topups(1)
    wallet.mark_topup_paid(topups[0]["id"])
    covered = wallet.apply_wallet_to_payment(1, 221)
    assert covered == 221
