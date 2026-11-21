import base64
import hashlib
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.config import CASHFREE_APP_ID, CASHFREE_ENV, CASHFREE_SECRET, MOCK_CASHFREE, PUBLIC_BASE_URL

PG_BASE = (
    "https://sandbox.cashfree.com/pg"
    if CASHFREE_ENV == "sandbox"
    else "https://api.cashfree.com/pg"
)
PG_HEADERS = {
    "x-client-id": CASHFREE_APP_ID,
    "x-client-secret": CASHFREE_SECRET,
    "x-api-version": "2025-01-01",
    "Content-Type": "application/json",
}

LINK_EXPIRY_MINUTES = 30
IST = timezone(timedelta(hours=5, minutes=30))


def payment_configuration() -> dict:
    """Safe diagnostics shared by the host's dashboard and voice backend."""
    missing = [name for name, value in (
        ("CASHFREE_APP_ID", CASHFREE_APP_ID), ("CASHFREE_SECRET", CASHFREE_SECRET)
    ) if not value.strip()]
    return {
        "mock": MOCK_CASHFREE,
        "environment": CASHFREE_ENV,
        "sms_enabled": not MOCK_CASHFREE and not missing,
        "missing_settings": missing,
        "message": (
            "SMS is disabled: MOCK_CASHFREE=1. Set MOCK_CASHFREE=0 and configure Cashfree credentials, then restart both the app and MCP server."
            if MOCK_CASHFREE else
            "Missing Cashfree credentials: " + ", ".join(missing)
            if missing else
            "Cashfree SMS is requested when a payment link is created. Handset delivery is not verified."
        ),
    }


def _expiry_iso() -> str:
    return (datetime.now(IST) + timedelta(minutes=LINK_EXPIRY_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def _normalize_phone(phone: str) -> str:
    """Cashfree wants a bare 10-digit Indian mobile number. Callers (voice
    agent, test harness) may hand us +91-prefixed, spaced, or hyphenated
    numbers - strip everything but digits and keep the last 10."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def create_payment_link(amount: int, customer_phone: str, purpose: str, order_id: int | None = None, customer_name: str | None = None, payment_details: dict | None = None) -> dict:
    """Returns link details and SMS request status (not a delivery receipt).

    Field shapes follow the Cashfree sandbox Create Payment Link API exactly
    (link_id alnum/hyphen/underscore max 50, link_expiry_time ISO with
    +05:30, link_notes capped at 5 pairs, link_partial_payments false since
    UPI doesn't support partial payments - any part-payment here is our own
    pay_now/on-account split, not Cashfree's partial-payment feature).
    """
    link_id = f"tokri-{uuid.uuid4().hex[:12]}"
    expires_at = _expiry_iso()
    sms_notification = {
        "status": "mocked" if MOCK_CASHFREE else "requested",
        "phone": _normalize_phone(customer_phone),
    }

    if MOCK_CASHFREE:
        return {"link_id": link_id, "link_url": f"https://payments-test.cashfree.com/links/{link_id}", "expires_at": expires_at, "sms_notification": sms_notification}

    configuration = payment_configuration()
    if configuration["missing_settings"]:
        raise RuntimeError(configuration["message"])

    notes = {"order_id": str(order_id)} if order_id else {}
    for key in ("order_total", "pay_now", "wallet_applied", "outstanding_new"):
        if payment_details and key in payment_details:
            notes[key] = str(payment_details[key])
    body = {
        "link_id": link_id,
        "link_amount": amount,
        "link_currency": "INR",
        "link_purpose": purpose,
        "customer_details": {"customer_phone": _normalize_phone(customer_phone), "customer_name": customer_name or ""},
        "link_expiry_time": expires_at,
        "link_notes": notes,
        "link_partial_payments": False,
        # Ask Cashfree to send this link to the saved customer mobile.
        # API acceptance is not confirmation of handset delivery.
        "link_notify": {"send_sms": True, "send_email": False},
    }
    if PUBLIC_BASE_URL:
        body["link_meta"] = {"notify_url": f"{PUBLIC_BASE_URL.rstrip('/')}/webhook/cashfree"}

    resp = httpx.post(f"{PG_BASE}/links", headers=PG_HEADERS, json=body, timeout=20)
    if resp.status_code >= 400:
        raise RuntimeError(f"Cashfree create-link failed ({resp.status_code}): {resp.text}")
    response_body = resp.json()
    return {"link_id": response_body["link_id"], "link_url": response_body["link_url"], "expires_at": expires_at, "sms_notification": sms_notification}


def cancel_payment_link(link_id: str) -> None:
    """Only ACTIVE links can be cancelled; a no-op in mock mode."""
    if MOCK_CASHFREE:
        return
    resp = httpx.post(f"{PG_BASE}/links/{link_id}/cancel", headers=PG_HEADERS, timeout=20)
    resp.raise_for_status()


def get_payment_link_status(link_id: str) -> str:
    if MOCK_CASHFREE:
        return "ACTIVE"
    return get_payment_link_details(link_id).get("link_status", "ACTIVE")


def get_payment_link_details(link_id: str) -> dict:
    if MOCK_CASHFREE:
        return {"link_id": link_id, "link_status": "ACTIVE"}
    resp = httpx.get(f"{PG_BASE}/links/{link_id}", headers=PG_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def verify_webhook_signature(timestamp: str, raw_body: bytes, signature: str) -> bool:
    signed_payload = timestamp.encode() + raw_body
    expected = base64.b64encode(
        hmac.new(CASHFREE_SECRET.encode(), signed_payload, hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)
