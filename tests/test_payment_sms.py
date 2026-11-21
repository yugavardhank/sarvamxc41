import httpx
import pytest

from app import pipeline
from app.services import cashfree, orders


@pytest.mark.parametrize("phone", ["9876543210", "+91 98765-43210"])
def test_real_link_requests_sms_to_customer_mobile(monkeypatch, phone):
    monkeypatch.setattr(cashfree, "MOCK_CASHFREE", False)
    requests = []

    def post(url, **kwargs):
        requests.append(kwargs["json"])
        return httpx.Response(200, json={
            "link_id": kwargs["json"]["link_id"],
            "link_url": "https://payments-test.cashfree.com/links/test",
        })

    monkeypatch.setattr(cashfree.httpx, "post", post)
    link = cashfree.create_payment_link(100, phone, "Test payment")
    assert len(requests) == 1
    assert requests[0]["customer_details"]["customer_phone"] == "9876543210"
    assert requests[0]["link_notify"] == {"send_sms": True, "send_email": False}
    assert link["sms_notification"] == {"status": "requested", "phone": "9876543210"}


def test_mock_link_never_sends_sms(monkeypatch):
    def unexpected_post(*args, **kwargs):
        pytest.fail("Mock payments must not send an SMS or create a real link")

    monkeypatch.setattr(cashfree.httpx, "post", unexpected_post)
    link = cashfree.create_payment_link(100, "+919876543210", "Test payment")
    assert link["sms_notification"] == {"status": "mocked", "phone": "9876543210"}


def test_payment_uses_saved_customer_phone_and_returns_sms_status(fresh_db):
    call = pipeline.start_call(7)
    pipeline.handle_transcript(call["order_id"], "toor dal", "en-IN")
    result = pipeline.plan_payment(call["order_id"], "full_now", None)
    assert result["status"] == "AWAITING_PAYMENT"
    assert result["sms_notification"] == {
        "status": "mocked",
        "phone": cashfree._normalize_phone(orders.get_customer(7)["phone"]),
    }
