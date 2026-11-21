import base64
import json
import re
import uuid

import httpx

from app.config import (
    MEDIA_DIR,
    MOCK_SARVAM,
    SARVAM_AGENT_PHONE_NUMBER,
    SARVAM_API_KEY,
    SARVAM_APP_ID,
    SARVAM_APP_VERSION,
    SARVAM_CONNECTION_ID,
    SARVAM_ORG_ID,
    SARVAM_VOICE_AGENTS_API_KEY,
    SARVAM_WORKSPACE_ID,
)
from app.services.catalog import ALIASES, list_brands, list_categories, resolve_alias

BASE_URL = "https://api.sarvam.ai"
HEADERS = {"api-subscription-key": SARVAM_API_KEY}

OUTBOUND_BASE_URL = "https://apps.sarvam.ai/api/outbounds"

CHAT_MODEL = "sarvam-105b"
STT_MODEL = "saaras:v4"
TTS_MODEL = "bulbul:v4-flash"

SAME_AGAIN_PATTERNS = [
    r"\bsame\s+(as\s+)?(last\s+time|again)\b",
    r"\busual\b",
    r"\bsame\s+order\b",
]

PRICE_OBJECTION_PATTERNS = [
    r"\btoo\s+(expensive|much|high)\b",
    r"\bbit\s+(expensive|much|high)\b",
    r"\b(reduce|lower)\s+(the\s+)?(price|total|cost)\b",
    r"\b(any\s+)?discount\b",
    r"\bcheaper\b",
    r"\bthat'?s\s+(a\s+lot|high|steep)\b",
    r"\bcan you do (it )?for less\b",
]

LIST_SAVE_PATTERN = re.compile(r"\bsave\s+(?:this|it)\s+as\s+(?:my\s+)?(.+?)(?:\s+list)?$")
LIST_REFERENCE_PATTERN = re.compile(r"(?:from|use)\s+(?:my\s+)?(.+?)\s+list\b|\bmy\s+(.+?)\s+list\b")


def transcribe(audio_path: str, language_code: str | None = None) -> dict:
    """Returns {"transcript": str, "language_code": str}."""
    if MOCK_SARVAM:
        return {
            "transcript": "same as last time",
            "language_code": language_code or "hi-IN",
        }

    with open(audio_path, "rb") as f:
        files = {"file": (audio_path, f)}
        data = {"model": STT_MODEL, "language_code": language_code or "unknown"}
        resp = httpx.post(
            f"{BASE_URL}/speech-to-text",
            headers=HEADERS,
            files=files,
            data=data,
            timeout=30,
        )
    resp.raise_for_status()
    body = resp.json()
    return {
        "transcript": body.get("transcript", ""),
        "language_code": body.get("language_code") or language_code or "hi-IN",
    }


def _chat(messages: list[dict], temperature: float = 0) -> str:
    if MOCK_SARVAM:
        return ""

    resp = httpx.post(
        f"{BASE_URL}/v1/chat/completions",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={
            "model": CHAT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def wants_same_as_last_time(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in SAME_AGAIN_PATTERNS)


def wants_price_objection(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in PRICE_OBJECTION_PATTERNS)


def extract_list_save_name(text: str) -> str | None:
    match = LIST_SAVE_PATTERN.search(text.lower())
    return match.group(1).strip() if match else None


def extract_list_reference_name(text: str) -> str | None:
    match = LIST_REFERENCE_PATTERN.search(text.lower())
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip()


def extract_payment_choice(text: str) -> dict:
    """Returns {"payment_choice": "full_now"|"part_now"|"on_account"|None,
    "pay_now_amount": float|None}."""
    lowered = text.lower()
    amount_match = re.search(r"pay\s*(?:rs\.?|rupees)?\s*(\d+)\s*now", lowered)
    pay_now_amount = float(amount_match.group(1)) if amount_match else None

    if re.search(r"\bon\s+account\b|\bpay\s+on\s+delivery\b|\blater\b|\bcredit\b", lowered):
        return {"payment_choice": "on_account", "pay_now_amount": pay_now_amount}
    if pay_now_amount is not None or re.search(r"\bpart\b|\bsome\s+now\b|\bhalf\b", lowered):
        return {"payment_choice": "part_now", "pay_now_amount": pay_now_amount}
    if re.search(r"\b(pay\s+)?(all|full|everything)\s+now\b|\bpay\s+now\b|\bpay\s+in\s+full\b", lowered):
        return {"payment_choice": "full_now", "pay_now_amount": pay_now_amount}
    return {"payment_choice": None, "pay_now_amount": pay_now_amount}


def outbound_calling_configured() -> bool:
    return bool(
        SARVAM_VOICE_AGENTS_API_KEY and SARVAM_ORG_ID and SARVAM_WORKSPACE_ID
        and SARVAM_APP_ID and SARVAM_CONNECTION_ID and SARVAM_AGENT_PHONE_NUMBER
    )


def trigger_outbound_call(user_phone_number: str, agent_variables: dict, webhook_url: str | None = None) -> dict:
    """Places a REAL outbound phone call via Sarvam's Instant Outbound API.
    Always real (no MOCK_SARVAM short-circuit) - there's no point faking a
    phone ringing. Raises if SARVAM_VOICE_AGENTS_API_KEY or any of
    ORG_ID/WORKSPACE_ID/APP_ID/CONNECTION_ID/AGENT_PHONE_NUMBER aren't set.

    Returns {"attempt_id": str}.
    """
    if not outbound_calling_configured():
        raise RuntimeError(
            "Outbound calling isn't configured yet - set SARVAM_VOICE_AGENTS_API_KEY, SARVAM_ORG_ID, "
            "SARVAM_WORKSPACE_ID, SARVAM_APP_ID, SARVAM_CONNECTION_ID and SARVAM_AGENT_PHONE_NUMBER in .env."
        )

    body = {
        "app_config": {
            "app_id": SARVAM_APP_ID,
            "app_version": SARVAM_APP_VERSION,
            "agent_variables": agent_variables,
            "connection_config": {
                "connection_id": SARVAM_CONNECTION_ID,
                "agent_phone_number": SARVAM_AGENT_PHONE_NUMBER,
            },
        },
        "user_config": {"user_phone_number": user_phone_number},
    }
    if webhook_url:
        body["webhook_config"] = {"url": webhook_url}

    url = f"{OUTBOUND_BASE_URL}/v1/orgs/{SARVAM_ORG_ID}/workspaces/{SARVAM_WORKSPACE_ID}/outbounds"
    resp = httpx.post(url, headers={"X-API-Key": SARVAM_VOICE_AGENTS_API_KEY, "Content-Type": "application/json"}, json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


def extract_items(text: str, language_code: str) -> dict:
    """Returns {"items": [{"name", "qty", "brand"}], "budget_hint": float|None, "language": str}."""
    if MOCK_SARVAM:
        return _mock_extract_items(text, language_code)

    system = (
        "You extract a grocery reorder list from a transcript of a phone call with "
        "an Indian grocery-reorder customer. Return JSON only, matching this schema "
        'exactly: {"items":[{"name":string,"qty":number|null,"brand":string|null}],'
        '"budget_hint":number|null,"language":string}. "name" should be a short '
        "category word like 'salt', 'toor dal', 'rice', not a full sentence."
    )
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": text}]
    )
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raw = _chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
                {"role": "user", "content": "Return valid JSON only, no prose."},
            ]
        )
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"items": [], "budget_hint": None, "language": language_code}
    data.setdefault("language", language_code)
    data.setdefault("budget_hint", None)
    return data


def speak(text: str, language_code: str) -> str:
    """Synthesizes speech and returns a path to the saved audio file under MEDIA_DIR."""
    filename = f"{uuid.uuid4().hex}.wav"
    out_path = MEDIA_DIR / filename

    if MOCK_SARVAM:
        out_path.write_bytes(b"")
        return str(out_path)

    resp = httpx.post(
        f"{BASE_URL}/text-to-speech",
        headers={**HEADERS, "Content-Type": "application/json"},
        json={"text": text[:2500], "language_code": language_code, "model": TTS_MODEL},
        timeout=20,
    )
    resp.raise_for_status()
    audios = resp.json()["audios"]
    audio_bytes = base64.b64decode(audios[0])
    out_path.write_bytes(audio_bytes)
    return str(out_path)


_NUMBER_WORDS = {
    "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3,
    "four": 4, "char": 4, "five": 5, "paanch": 5,
}


_BUDGET_PHRASE = re.compile(r"(?:under|budget|within)\s*(?:rs\.?|rupees)?\s*(\d+)")


def _extract_qty(chunk: str) -> int | None:
    # Strip any "under/budget/within <n>" phrase first, so a per-item budget
    # hint like "salt under 30" isn't mistaken for a quantity of 30.
    chunk = _BUDGET_PHRASE.sub("", chunk)
    match = re.search(r"\b(\d+)\s*(kg|l|litre|liter|packet|packets|pack)?\b", chunk)
    if match:
        return int(match.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", chunk):
            return value
    return None


def _extract_budget(text: str) -> float | None:
    match = _BUDGET_PHRASE.search(text)
    if match:
        return float(match.group(1))
    return None


def _mock_extract_items(text: str, language_code: str) -> dict:
    """A small rule-based stand-in for the real LLM, so the demo console gives
    a live, text-dependent response without a Sarvam key. A real key replaces
    this entirely. Splits the transcript into chunks and matches each against
    the catalog's known categories/brands by substring.
    """
    lowered = text.lower()
    budget_hint = _extract_budget(lowered)

    category_words = sorted(
        list(list_categories()) + [w for words in ALIASES.values() for w in words],
        key=len,
        reverse=True,
    )
    brands = sorted(list_brands(), key=len, reverse=True)

    chunks = re.split(r",|\baur\b|\band\b", lowered)
    items = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        matched_word = next((c for c in category_words if c in chunk), None)
        category = resolve_alias(matched_word) or matched_word if matched_word else None
        brand = next((b for b in brands if b.lower() in chunk), None)
        qty = _extract_qty(chunk)
        if category:
            items.append({"name": category, "qty": qty, "brand": brand})
        elif _looks_like_a_single_item_name(chunk):
            # Doesn't match a known category - still surfaced as a requested
            # item (rather than silently dropped) so the pipeline can tell
            # the customer it wasn't found, same as a real catalog search
            # miss would.
            items.append({"name": chunk, "qty": qty, "brand": brand})

    return {"items": items, "budget_hint": budget_hint, "language": language_code}


_FILLER_WORDS = {"nothing", "too", "expensive", "cheap", "please", "order", "this", "time", "some"}


def _looks_like_a_single_item_name(chunk: str) -> bool:
    words = [w for w in re.findall(r"[a-z]+", chunk) if w not in _FILLER_WORDS]
    return 0 < len(words) <= 2
