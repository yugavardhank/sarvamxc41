import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app.config import media_url
from app.db import get_conn, log_event
from app.pipeline import (
    accept_offer,
    cancel_order,
    decline_further_negotiation,
    handle_transcript,
    mark_paid,
    plan_payment,
    start_call,
)
from app.services import lists, orders, policy, sarvam, wallet

router = APIRouter()


@router.get("/api/customers")
def list_customers():
    result = []
    for customer in orders.list_customers():
        result.append({**customer, "profile": policy.profile(customer)})
    return result


@router.get("/api/catalog")
def list_catalog():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY category, price").fetchall()
    return [dict(r) for r in rows]


@router.get("/api/orders")
def list_all_orders():
    result = []
    for order in orders.list_orders():
        order["items"] = orders.get_order_items(order["id"])
        order["concessions"] = orders.get_concessions(order["id"])
        result.append(order)
    return result


@router.get("/api/events")
def list_events(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/lists/{customer_id}")
def get_saved_lists(customer_id: int):
    result = []
    for saved in lists.list_saved_lists(customer_id):
        saved["items"] = lists.get_list_items(saved["id"])
        result.append(saved)
    return result


@router.post("/dev/start-call")
def dev_start_call(customer_id: int = Form(...)):
    result = start_call(customer_id)
    audio_path = sarvam.speak(result["greeting_text"], result["language_code"])
    return {**result, "audio_url": media_url(audio_path)}


@router.post("/dev/simulate-voice")
async def dev_simulate_voice(
    order_id: int = Form(...),
    audio: Optional[UploadFile] = None,
    text: Optional[str] = Form(None),
):
    order = orders.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    customer = orders.get_customer(order["customer_id"])
    language_code = customer["language"] or "en-IN"

    if audio is not None:
        suffix = Path(audio.filename or "audio.wav").suffix or ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await audio.read())
            audio_path = tmp.name
        stt = sarvam.transcribe(audio_path, language_code)
        transcript = stt["transcript"]
        language_code = stt["language_code"]
    else:
        transcript = text or ""

    result = handle_transcript(order_id, transcript, language_code)
    reply_audio_path = sarvam.speak(result["reply_text"], language_code)
    log_event("simulate_voice_reply", order["customer_id"], result["reply_text"])

    return {
        "order_id": order_id,
        "transcript": transcript,
        "status": result["status"],
        "items": result.get("items"),
        "total": result.get("total"),
        "offer": result.get("offer"),
        "reply_text": result["reply_text"],
        "audio_url": media_url(reply_audio_path),
    }


@router.post("/dev/accept-offer")
def dev_accept_offer(order_id: int = Form(...)):
    result = accept_offer(order_id)
    customer = orders.get_customer(orders.get_order(order_id)["customer_id"])
    audio_path = sarvam.speak(result["reply_text"], customer["language"] or "en-IN")
    return {**result, "order_id": order_id, "audio_url": media_url(audio_path)}


@router.post("/dev/decline-offer")
def dev_decline_offer(order_id: int = Form(...)):
    result = decline_further_negotiation(order_id)
    return {**result, "order_id": order_id}


@router.post("/dev/pay")
def dev_pay(order_id: int = Form(...), payment_choice: str = Form("full_now"), pay_now_amount: Optional[float] = Form(None)):
    result = plan_payment(order_id, payment_choice, pay_now_amount)
    customer = orders.get_customer(orders.get_order(order_id)["customer_id"])
    audio_path = sarvam.speak(result["reply_text"], customer["language"] or "en-IN")
    return {**result, "order_id": order_id, "audio_url": media_url(audio_path)}


@router.post("/dev/cancel-order")
def dev_cancel_order(order_id: int = Form(...)):
    return {**cancel_order(order_id), "order_id": order_id}


@router.post("/dev/mark-paid")
def dev_mark_paid(order_id: int = Form(...)):
    """Dev-only convenience for the dashboard's demo button: simulates a paid
    Cashfree webhook without needing a real signature, since /webhook/cashfree
    itself must keep enforcing signature verification once real keys are in use."""
    order = orders.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return {**mark_paid(order_id), "order_id": order_id}


@router.post("/dev/wallet/topup")
def dev_wallet_topup(customer_id: int = Form(...), amount: int = Form(...)):
    return wallet.create_topup(customer_id, amount)


@router.get("/api/wallet/{customer_id}/topups")
def dev_wallet_topups(customer_id: int):
    return wallet.list_topups(customer_id)


@router.post("/dev/wallet/mark-paid")
def dev_wallet_mark_paid(topup_id: int = Form(...)):
    return wallet.mark_topup_paid(topup_id)
