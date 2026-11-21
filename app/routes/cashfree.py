from fastapi import APIRouter, HTTPException, Request

from app.config import MOCK_CASHFREE
from app.db import log_event
from app.pipeline import mark_paid
from app.services import cashfree, orders, wallet

router = APIRouter()


@router.post("/webhook/cashfree")
async def cashfree_webhook(request: Request):
    raw_body = await request.body()

    if not MOCK_CASHFREE:
        signature = request.headers.get("x-webhook-signature", "")
        timestamp = request.headers.get("x-webhook-timestamp", "")
        if not cashfree.verify_webhook_signature(timestamp, raw_body, signature):
            raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    event_type = payload.get("type", "")
    data = payload.get("data", {})
    log_event("cashfree_webhook", detail=f"type={event_type}")

    if event_type == "PAYMENT_LINK_EVENT" or "link" in data:
        link = data.get("link", data)
        link_id = link.get("link_id")
        link_status = link.get("link_status")
        if link_id and link_status == "PAID":
            order = orders.get_order_by_link_id(link_id)
            if order:
                mark_paid(order["id"])
            else:
                topup = wallet.get_topup_by_link_id(link_id)
                if topup:
                    wallet.mark_topup_paid(topup["id"])
        return {"ok": True}

    return {"ok": True}
