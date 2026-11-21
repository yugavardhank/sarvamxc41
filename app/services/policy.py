"""Merchant-set negotiation policy: tiers, churn risk, credit, and the
per-order discount cap. These are the limits the voice agent may never
exceed - it can only offer concessions this module says are valid.

Thresholds below match tokri_Database.xlsx's Policy sheet exactly.
"""
from datetime import datetime, timezone

from app.db import get_conn

CONFIRMED_STATUSES = ("CONFIRMED", "ON_ACCOUNT")

FREE_DELIVERY_THRESHOLD = 500
DELIVERY_FEE = 30

GOLD_MIN_ORDERS_90D = 6
GOLD_MIN_ON_TIME_RATE = 90.0
SILVER_MIN_ORDERS_90D = 2
CHURN_RISK_DAYS = 30
CREDIT_BLOCKED_ON_TIME_RATE = 70.0

# (max_discount_pct, prepay_only)
TIER_CAPS = {
    "Gold": (5.0, False),
    "Silver": (4.0, False),
    "New": (2.0, True),
}
CHURN_RISK_CAP = (7.0, False)  # win-back offer, overrides Gold/Silver's own cap
CREDIT_BLOCKED_CAP_PCT = 3.0  # a ceiling, not a flat replacement - see discount_cap()


def customer_signals(customer: dict) -> dict:
    """orders_90d/avg_basket/last_order_days, each combining the merchant's
    own baseline (seeded from tokri_Database.xlsx) with whatever the
    customer has actually ordered through Tokri since - so a customer's tier
    keeps moving as they use Tokri, it isn't frozen at the seed snapshot.
    """
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT total, created_at FROM orders
            WHERE customer_id = ? AND status IN {CONFIRMED_STATUSES}
            ORDER BY created_at DESC
            """,
            (customer["id"],),
        ).fetchall()

    now = datetime.now(timezone.utc)
    real_orders_90d = 0
    real_basket_sum = 0
    most_recent_real = None
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        if most_recent_real is None or created > most_recent_real:
            most_recent_real = created
        if (now - created).days <= 90:
            real_orders_90d += 1
            real_basket_sum += row["total"]

    seed_orders = customer["orders_90d_seed"] or 0
    seed_avg = customer["avg_basket_seed"] or 0

    total_orders = seed_orders + real_orders_90d
    total_sum = seed_orders * seed_avg + real_basket_sum
    avg_basket = round(total_sum / total_orders) if total_orders else 0

    candidates = []
    if customer["last_order_at"]:
        candidates.append(datetime.fromisoformat(customer["last_order_at"]))
    if most_recent_real is not None:
        candidates.append(most_recent_real)
    last_order_days = (now - max(candidates)).days if candidates else None

    return {"orders_90d": total_orders, "avg_basket": avg_basket, "last_order_days": last_order_days}


def tier(signals: dict, on_time_rate: float) -> str:
    if signals["orders_90d"] >= GOLD_MIN_ORDERS_90D and on_time_rate >= GOLD_MIN_ON_TIME_RATE:
        return "Gold"
    if signals["orders_90d"] >= SILVER_MIN_ORDERS_90D:
        return "Silver"
    return "New"


def is_churn_risk(signals: dict, customer_tier: str) -> bool:
    if customer_tier == "New":
        return False
    return signals["last_order_days"] is not None and signals["last_order_days"] > CHURN_RISK_DAYS


def is_credit_blocked(customer: dict) -> bool:
    if customer["credit_limit"] <= 0:
        return False
    on_time_rate = customer["on_time_rate"] if customer["on_time_rate"] is not None else 100.0
    return customer["outstanding"] >= customer["credit_limit"] or on_time_rate < CREDIT_BLOCKED_ON_TIME_RATE


def credit_available(customer: dict) -> int:
    if is_credit_blocked(customer):
        return 0
    return max(0, customer["credit_limit"] - customer["outstanding"])


def discount_cap(customer: dict, signals: dict, customer_tier: str, churn_risk: bool) -> tuple[float, bool]:
    """Returns (max_discount_pct, prepay_only).

    Credit-blocked acts as a *ceiling* on top of whatever the tier/churn
    cap would otherwise be (min(3%, that cap)), not a flat 3% - a New
    customer who is also credit-blocked still only gets 2%, not 3%.
    """
    if churn_risk and customer_tier in ("Gold", "Silver"):
        base_cap, base_prepay = CHURN_RISK_CAP
    else:
        base_cap, base_prepay = TIER_CAPS.get(customer_tier, TIER_CAPS["New"])

    if is_credit_blocked(customer):
        return (min(CREDIT_BLOCKED_CAP_PCT, base_cap), True)
    return (base_cap, base_prepay)


def profile(customer: dict) -> dict:
    """Full computed profile used by /session/start and the dashboard."""
    if customer.get("data_status") == "INCOMPLETE":
        # Per the merchant's own data rules: until the agent asks the
        # missing question and gets an answer, treat as New (prepay only,
        # lowest cap) rather than guessing a tier from partial data.
        return {
            "tier": "New", "churn_risk": False, "credit_blocked": False, "credit_available": 0,
            "max_discount_pct": TIER_CAPS["New"][0], "prepay_only": True,
            "orders_90d": customer.get("orders_90d_seed") or 0, "avg_basket": customer.get("avg_basket_seed") or 0,
            "last_order_days": None, "data_status": "INCOMPLETE",
            "missing_fields": customer.get("missing_fields"), "agent_question": customer.get("agent_question"),
        }

    signals = customer_signals(customer)
    on_time_rate = customer["on_time_rate"] if customer["on_time_rate"] is not None else 100.0
    customer_tier = tier(signals, on_time_rate)
    churn_risk = is_churn_risk(signals, customer_tier)
    blocked = is_credit_blocked(customer)
    cap_pct, prepay_only = discount_cap(customer, signals, customer_tier, churn_risk)
    return {
        "tier": customer_tier,
        "churn_risk": churn_risk,
        "credit_blocked": blocked,
        "credit_available": credit_available(customer),
        "max_discount_pct": cap_pct,
        "prepay_only": prepay_only,
        "orders_90d": signals["orders_90d"],
        "avg_basket": signals["avg_basket"],
        "last_order_days": signals["last_order_days"],
        "data_status": "COMPLETE",
        "missing_fields": None,
        "agent_question": customer.get("agent_question"),
    }
