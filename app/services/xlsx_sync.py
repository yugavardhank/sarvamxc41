"""Reads tokri_Database.xlsx directly off disk and syncs it into the live
database - the spreadsheet itself is the source of truth, not a one-time
copy baked into code. Call sync() any time (startup, or on demand via
/dev/reload-inventory) to pick up edits made to the file.

Products are upserted by sku; customers are upserted by phone, updating
only the fields the sheet actually owns (name, email, language, credit
limit/outstanding, on-time rate, order-history baseline, data-completeness
flags) - wallet_balance and anything accrued through live testing in this
app are left alone.
"""
from datetime import datetime, timedelta, timezone

import openpyxl

from app.db import get_conn, log_event

LANG_CODES = {"Hindi": "hi-IN", "Kannada": "kn-IN", "English": "en-IN"}


def _read_products(ws) -> list[tuple]:
    products = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        (sku, item_name, brand, product_name, category, pack_size, unit, price_tier,
         price, cost, floor_price, stock, reorder, status, margin, alt_sku, saving,
         bundle2, al_hi, al_kn) = row
        in_stock = 0 if status == "OUT_OF_STOCK" else 1
        products.append((
            sku, product_name, item_name.lower(), brand, int(price), int(round(floor_price)),
            in_stock, alt_sku, int(round(bundle2)) if bundle2 else None,
        ))
    return products


def _read_customers(ws) -> list[tuple]:
    customers = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        (cid, name, phone, email, lang, orders_90d, avg_basket, on_time, last_order_days,
         credit_limit, outstanding, credit_avail, tier, churn, blocked, cap, prepay,
         data_status, missing_fields, agent_question, scenario) = row
        lang_code = LANG_CODES.get(lang)
        on_time_pct = round(on_time * 100, 1) if on_time is not None else None
        customers.append((
            cid, name, str(phone), email, lang_code, orders_90d, avg_basket, on_time_pct,
            last_order_days, credit_limit or 0, outstanding or 0, data_status, missing_fields,
            agent_question,
        ))
    return customers


def sync(path: str) -> dict:
    wb = openpyxl.load_workbook(path, data_only=True)
    products = _read_products(wb["Inventory_Data"])
    customers = _read_customers(wb["User_Data"])

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO products (sku, name, category, brand, price, floor_price, in_stock, bundle_price_2)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (sku) DO UPDATE SET
                name=excluded.name, category=excluded.category, brand=excluded.brand,
                price=excluded.price, floor_price=excluded.floor_price,
                in_stock=excluded.in_stock, bundle_price_2=excluded.bundle_price_2
            """,
            [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[8]) for p in products],
        )
        conn.executemany(
            "UPDATE products SET alt_sku = ? WHERE sku = ?",
            [(p[7], p[0]) for p in products if p[7]],
        )

        for c in customers:
            (external_id, name, phone, email, language, orders_90d, avg_basket, on_time_rate,
             last_order_days, credit_limit, outstanding, data_status, missing_fields, agent_question) = c
            last_order_at = (
                (datetime.now(timezone.utc) - timedelta(days=last_order_days)).isoformat()
                if last_order_days is not None
                else None
            )
            conn.execute(
                """
                INSERT INTO customers
                    (external_id, name, phone, email, language, credit_limit, outstanding,
                     on_time_rate, orders_90d_seed, avg_basket_seed, last_order_at,
                     data_status, missing_fields, agent_question)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (phone) DO UPDATE SET
                    external_id=excluded.external_id, name=excluded.name, email=excluded.email,
                    language=excluded.language, credit_limit=excluded.credit_limit,
                    outstanding=excluded.outstanding, on_time_rate=excluded.on_time_rate,
                    orders_90d_seed=excluded.orders_90d_seed, avg_basket_seed=excluded.avg_basket_seed,
                    last_order_at=excluded.last_order_at, data_status=excluded.data_status,
                    missing_fields=excluded.missing_fields, agent_question=excluded.agent_question
                """,
                (
                    external_id, name, phone, email, language, credit_limit, outstanding,
                    on_time_rate, orders_90d, avg_basket, last_order_at,
                    data_status, missing_fields, agent_question,
                ),
            )

    log_event("xlsx_sync", detail=f"synced {len(products)} products, {len(customers)} customers from {path}")
    return {"products": len(products), "customers": len(customers)}
