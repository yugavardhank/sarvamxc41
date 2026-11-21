from app.services import catalog


def test_prefers_known_brand_over_cheapest(fresh_db):
    catalog.upsert_preference(7, "toor dal", "24 Mantra", 2)
    result = catalog.match_item(7, "toor dal", qty=None, brand_hint=None, budget_hint=None)
    assert result["matched_sku"] == "DAL-01"  # 24 Mantra, even though Fortune (DAL-03) is cheaper
    assert result["qty"] == 2  # usual_qty from the preference


def test_falls_back_to_cheapest_without_preference(fresh_db):
    result = catalog.match_item(7, "toor dal", qty=2, brand_hint=None, budget_hint=None)
    assert result["matched_sku"] == "DAL-03"  # Fortune, the cheapest tier
    assert result["qty"] == 2
    assert result["line_total"] == result["unit_price"] * 2


def test_respects_budget_hint(fresh_db):
    result = catalog.match_item(7, "oil", qty=1, brand_hint=None, budget_hint=145)
    assert result["unit_price"] <= 145


def test_explicit_brand_hint_overrides_preference(fresh_db):
    catalog.upsert_preference(7, "toor dal", "Fortune", 1)
    result = catalog.match_item(7, "toor dal", qty=1, brand_hint="24 Mantra", budget_hint=None)
    assert result["matched_sku"] == "DAL-01"


def test_unmatched_item_returns_no_sku(fresh_db):
    result = catalog.match_item(7, "paneer", qty=1, brand_hint=None, budget_hint=None)
    assert result["matched_sku"] is None
    assert result["line_total"] == 0


def test_alias_resolves_to_canonical_category(fresh_db):
    # "namak" (Hindi) and "uppu" (Kannada) should both resolve to salt.
    hindi = catalog.match_item(7, "namak", qty=1, brand_hint=None, budget_hint=None)
    kannada = catalog.match_item(7, "uppu", qty=1, brand_hint=None, budget_hint=None)
    assert hindi["category"] == kannada["category"] == "iodised salt"


def test_out_of_stock_product_has_an_in_stock_substitute(fresh_db):
    # ATT-01 (Aashirvaad Atta) is deliberately seeded OUT_OF_STOCK with
    # ATT-02 (Pillsbury) as its alt_sku - exactly the substitution scenario
    # the real inventory data was built to exercise.
    att01 = catalog.get_product("ATT-01")
    assert att01["in_stock"] == 0
    substitute = catalog.find_in_stock_substitute(att01)
    assert substitute["sku"] == "ATT-02"
    assert substitute["in_stock"] == 1
