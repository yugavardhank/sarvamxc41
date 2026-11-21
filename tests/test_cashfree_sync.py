import httpx
import pytest

from app import pipeline
from app.services import cashfree, orders


def draft_order():
    order_id = pipeline.start_call(7)["order_id"]
    pipeline.handle_transcript(order_id, "toor dal", "en-IN")
    return order_id


def test_link_contains_payment_breakdown(fresh_db, monkeypatch):
    monkeypatch.setattr(cashfree, "MOCK_CASHFREE", False)
    monkeypatch.setattr(cashfree, "CASHFREE_APP_ID", "test-app")
    monkeypatch.setattr(cashfree, "CASHFREE_SECRET", "test-secret")
    requests = []

    def post(url, **kwargs):
        body = kwargs["json"]
        requests.append(body)
        return httpx.Response(200, json={"link_id": body["link_id"], "link_url": "https://example.test/pay"})

    monkeypatch.setattr(cashfree.httpx, "post", post)
    orders.credit_wallet(7, 50)
    order_id = draft_order()
    total = orders.get_order(order_id)["total"]
    pipeline.apply_payment_plan(order_id, total)
    body = requests[0]
    assert body["link_amount"] == total - 50
    assert body["customer_details"]["customer_name"] == orders.get_customer(7)["name"]
    assert body["link_notes"] == {
        "order_id": str(order_id), "order_total": str(total), "pay_now": str(total),
        "wallet_applied": "50", "outstanding_new": "0",
    }
    assert body["link_notify"]["send_sms"] is True


def test_failed_cashfree_request_does_not_debit_wallet(fresh_db, monkeypatch):
    orders.credit_wallet(7, 50)
    order_id = draft_order()
    before = orders.get_order(order_id)

    def fail(**kwargs):
        raise RuntimeError("Cashfree unavailable")

    monkeypatch.setattr(cashfree, "create_payment_link", fail)
    with pytest.raises(RuntimeError, match="Cashfree unavailable"):
        pipeline.apply_payment_plan(order_id, before["total"])
    assert orders.get_customer(7)["wallet_balance"] == 50
    assert orders.get_order(order_id) == before


@pytest.mark.parametrize("provider_status", ["ACTIVE", "PAID", "EXPIRED"])
def test_sync_confirms_only_fully_paid_links(fresh_db, monkeypatch, provider_status):
    order_id = draft_order()
    pipeline.plan_payment(order_id, "full_now", None)
    order = orders.get_order(order_id)
    monkeypatch.setattr(cashfree, "get_payment_link_details", lambda _: {
        "link_id": order["link_id"], "link_status": provider_status,
        "link_amount": order["pay_now"], "link_amount_paid": order["pay_now"], "link_currency": "INR",
    })
    result = pipeline.sync_payment(order_id)
    assert result["status"] == ("CONFIRMED" if provider_status == "PAID" else "AWAITING_PAYMENT")
    assert result["payment_status"] == provider_status
    assert pipeline.sync_payment(order_id)["status"] == result["status"]


@pytest.mark.parametrize("mismatch", [
    {"link_amount_paid": 1}, {"link_currency": "USD"}, {"link_id": "different-link"},
])
def test_sync_rejects_mismatched_payment(fresh_db, monkeypatch, mismatch):
    order_id = draft_order()
    pipeline.plan_payment(order_id, "full_now", None)
    order = orders.get_order(order_id)
    details = {"link_id": order["link_id"], "link_status": "PAID", "link_currency": "INR",
               "link_amount": order["pay_now"], "link_amount_paid": order["pay_now"]}
    details.update(mismatch)
    monkeypatch.setattr(cashfree, "get_payment_link_details", lambda _: details)
    with pytest.raises(ValueError):
        pipeline.sync_payment(order_id)
    assert orders.get_order(order_id)["status"] == "AWAITING_PAYMENT"
