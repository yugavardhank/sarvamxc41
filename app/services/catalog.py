from app.db import get_conn

# Local-language aliases per category, imported from tokri_Database.xlsx
# (Inventory_Data!aliases_hindi / aliases_kannada, pipe-separated, deduped).
ALIASES = {
    "ghee": ["desi ghee", "ghee", "tuppa"],
    "wheat flour (atta)": ["aata", "atta", "gehu ka atta", "godhi", "godhi hittu"],
    "toor dal": ["arhar dal", "bele", "dal", "togari bele", "toor dal"],
    "refined sunflower oil": ["adige enne", "enne", "oil", "refined tel", "tel"],
    "basmati rice": ["akki", "anna", "basmati", "chawal", "rice"],
    "milk": ["doodh", "haal", "haalu", "milk"],
    "sugar": ["cheeni", "sakkare", "shakkar", "sugar"],
    "onion": ["eerulli", "irulli", "onion", "pyaaz", "pyaz"],
    "potato": ["aalu", "aloo", "alugadde", "potato"],
    "iodised salt": ["namak", "salt", "uppu"],
}


def list_categories() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT category FROM products").fetchall()
    return [r["category"] for r in rows]


def list_brands() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT brand FROM products").fetchall()
    return [r["brand"] for r in rows]


def list_alias_words() -> list[str]:
    return [word for words in ALIASES.values() for word in words]


def resolve_alias(word: str) -> str | None:
    for category, words in ALIASES.items():
        if word in words:
            return category
    return None


def get_product(sku: str | None) -> dict | None:
    if not sku:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
    return dict(row) if row else None


def find_in_stock_substitute(product: dict) -> dict | None:
    """Walks the alt_sku chain (Premium -> Mid -> Economy) from an
    out-of-stock product until it finds one that's actually in stock.
    """
    seen = set()
    current = product
    while current and current["alt_sku"] and current["alt_sku"] not in seen:
        seen.add(current["alt_sku"])
        current = get_product(current["alt_sku"])
        if current and current["in_stock"]:
            return current
    return None


def get_preference(customer_id: int, category: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM preferences WHERE customer_id = ? AND category = ?",
            (customer_id, category),
        ).fetchone()
    return dict(row) if row else None


def upsert_preference(customer_id: int, category: str, brand: str | None, qty: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO preferences (customer_id, category, preferred_brand, usual_qty)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (customer_id, category)
            DO UPDATE SET preferred_brand = excluded.preferred_brand, usual_qty = excluded.usual_qty
            """,
            (customer_id, category, brand, qty),
        )


def _find_candidates(requested_name: str) -> list[dict]:
    """Keyword-matches the requested item text (or a known alias) against
    product name/category.

    This is a stand-in for a real catalog search (the supermarket's own
    inventory feed in production); it is deliberately simple substring
    matching, since the point being demoed is the ranking/negotiation logic
    downstream, not search relevance.
    """
    word = requested_name.strip().lower()
    word = resolve_alias(word) or word
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM products WHERE in_stock = 1").fetchall()
    candidates = []
    for row in rows:
        product = dict(row)
        category = product["category"].lower()
        name = product["name"].lower()
        if word == category or word in category or category in word or word in name:
            candidates.append(product)
    return candidates


def match_item(customer_id: int, requested_name: str, qty: int | None, brand_hint: str | None, budget_hint: float | None) -> dict:
    """Picks a product for one requested item: the customer's known brand
    preference for that category if available and in stock, otherwise the
    cheapest candidate within budget.
    """
    candidates = _find_candidates(requested_name)
    if not candidates:
        return {
            "requested_text": requested_name,
            "matched_sku": None,
            "product_name": None,
            "category": None,
            "brand": None,
            "qty": qty or 1,
            "unit_price": 0,
            "line_total": 0,
        }

    category = candidates[0]["category"]
    preference = get_preference(customer_id, category)

    chosen = None
    if brand_hint:
        for product in candidates:
            if product["brand"].lower() == brand_hint.lower():
                chosen = product
                break

    if chosen is None and preference and preference.get("preferred_brand"):
        for product in candidates:
            if product["brand"].lower() == preference["preferred_brand"].lower():
                chosen = product
                break

    if chosen is None:
        in_budget = [p for p in candidates if not budget_hint or p["price"] <= budget_hint]
        pool = in_budget or candidates
        chosen = sorted(pool, key=lambda p: p["price"])[0]

    final_qty = qty or (preference["usual_qty"] if preference else 1)
    return {
        "requested_text": requested_name,
        "matched_sku": chosen["sku"],
        "product_name": chosen["name"],
        "category": chosen["category"],
        "brand": chosen["brand"],
        "qty": final_qty,
        "unit_price": chosen["price"],
        "line_total": chosen["price"] * final_qty,
    }


def build_basket(customer_id: int, items: list[dict], budget_hint: float | None) -> list[dict]:
    return [
        match_item(customer_id, item["name"], item.get("qty"), item.get("brand"), budget_hint)
        for item in items
    ]
