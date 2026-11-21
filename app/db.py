import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT UNIQUE,
    phone TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    language TEXT,
    reorder_cadence_days INTEGER NOT NULL DEFAULT 7,
    last_called_at TEXT,
    default_budget INTEGER NOT NULL DEFAULT 1000,
    credit_limit INTEGER NOT NULL DEFAULT 0,
    outstanding INTEGER NOT NULL DEFAULT 0,
    wallet_balance INTEGER NOT NULL DEFAULT 0,
    on_time_rate REAL,
    -- Baseline signals from the merchant's own records, read on top of any
    -- real orders placed through Tokri (see policy.customer_signals): a
    -- customer's tier can keep moving as they actually use Tokri, it isn't
    -- frozen at whatever the seed data said on day one.
    orders_90d_seed INTEGER,
    avg_basket_seed INTEGER,
    last_order_at TEXT,
    data_status TEXT NOT NULL DEFAULT 'COMPLETE',
    missing_fields TEXT,
    agent_question TEXT
);

CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    price INTEGER NOT NULL,
    floor_price INTEGER NOT NULL,
    in_stock INTEGER NOT NULL DEFAULT 1,
    alt_sku TEXT REFERENCES products(sku),
    bundle_price_2 INTEGER
);

CREATE TABLE IF NOT EXISTS preferences (
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    category TEXT NOT NULL,
    preferred_brand TEXT,
    usual_qty INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (customer_id, category)
);

-- Order statuses: REQUESTED, BASKET_DRAFT, NEGOTIATING, AWAITING_PAYMENT,
-- CONFIRMED, ON_ACCOUNT, EXPIRED, CANCELLED.
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    subtotal INTEGER NOT NULL DEFAULT 0,
    delivery_fee INTEGER NOT NULL DEFAULT 0,
    discount_total INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    pay_now INTEGER,
    outstanding_new INTEGER,
    offered_rung INTEGER NOT NULL DEFAULT 0,
    wallet_applied INTEGER NOT NULL DEFAULT 0,
    link_id TEXT,
    link_url TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    requested_text TEXT NOT NULL,
    matched_sku TEXT REFERENCES products(sku),
    category TEXT,
    brand TEXT,
    qty INTEGER NOT NULL DEFAULT 1,
    unit_price INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS concessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    offer_type TEXT NOT NULL,
    description TEXT NOT NULL,
    saving INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_topups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount INTEGER NOT NULL,
    link_id TEXT,
    link_url TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL REFERENCES saved_lists(id),
    requested_text TEXT NOT NULL,
    matched_sku TEXT REFERENCES products(sku),
    category TEXT,
    brand TEXT,
    qty INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    customer_id INTEGER,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def log_event(kind: str, customer_id: int | None = None, detail: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, customer_id, detail) VALUES (?, ?, ?, ?)",
            (now(), kind, customer_id, detail),
        )
    print(f"[event] {kind} customer={customer_id} detail={detail}")


# Imported from tokri_Database.xlsx (Inventory_Data sheet): 30 SKUs across 10
# item types, 3 price tiers each (Premium/Mid/Economy). alt_sku chains
# downward (Premium -> Mid -> Economy -> None) for the brand-swap concession;
# floor_price and bundle_price_2 are the merchant's own numbers (cost-plus-
# margin and a 2-unit bundle price), never shown to the customer directly -
# only used as hard limits / concession math.
# (sku, name, category, brand, price, floor_price, in_stock, alt_sku, bundle_price_2)
PRODUCTS = [
    ("GHE-01", "Gowardhan Pure Cow Ghee 500 ml", "ghee", "Gowardhan", 395, 373, 1, "GHE-02", 766),
    ("ATT-01", "Aashirvaad Select Atta 5 kg", "wheat flour (atta)", "Aashirvaad", 310, 293, 0, "ATT-02", 601),
    ("DAL-01", "24 Mantra Organic Toor Dal 1 kg", "toor dal", "24 Mantra", 265, 250, 1, "DAL-02", 514),
    ("OIL-01", "Saffola Gold Oil 1 L", "refined sunflower oil", "Saffola", 218, 208, 1, "OIL-02", 423),
    ("RIC-01", "India Gate Basmati Classic 1 kg", "basmati rice", "India Gate", 168, 155, 1, "RIC-02", 326),
    ("MLK-01", "Amul Gold Full Cream Milk 1 L", "milk", "Amul", 72, 69, 1, "MLK-02", 140),
    ("SUG-01", "Madhur Sulphurless Sugar 1 kg", "sugar", "Madhur", 68, 66, 1, "SUG-02", 132),
    ("ONI-01", "Farm Select Nashik Onion 1 kg", "onion", "Farm Select", 48, 39, 1, "ONI-02", 93),
    ("POT-01", "Farm Select Table Potato 1 kg", "potato", "Farm Select", 46, 39, 1, "POT-02", 89),
    ("SLT-01", "Tata Salt Lite 1 kg", "iodised salt", "Tata", 45, 40, 1, "SLT-02", 87),
    ("GHE-02", "Amul Pure Ghee 500 ml", "ghee", "Amul", 340, 321, 1, "GHE-03", 660),
    ("ATT-02", "Pillsbury Chakki Fresh Atta 5 kg", "wheat flour (atta)", "Pillsbury", 262, 248, 1, "ATT-03", 508),
    ("DAL-02", "Tata Sampann Toor Dal 1 kg", "toor dal", "Tata Sampann", 205, 194, 1, "DAL-03", 398),
    ("OIL-02", "Fortune Sunlite Sunflower Oil 1 L", "refined sunflower oil", "Fortune", 152, 145, 1, "OIL-03", 295),
    ("RIC-02", "Daawat Rozana Basmati 1 kg", "basmati rice", "Daawat", 125, 116, 1, "RIC-03", 242),
    ("MLK-02", "Mother Dairy Toned Milk 1 L", "milk", "Mother Dairy", 62, 60, 1, "MLK-03", 120),
    ("SUG-02", "Dhampur Green Sugar 1 kg", "sugar", "Dhampur", 58, 56, 1, "SUG-03", 113),
    ("POT-02", "FreshPick Washed Potato 1 kg", "potato", "FreshPick", 38, 32, 1, "POT-03", 74),
    ("ONI-02", "FreshPick Onion 1 kg", "onion", "FreshPick", 38, 31, 1, "ONI-03", 74),
    ("SLT-02", "Tata Salt 1 kg", "iodised salt", "Tata", 28, 25, 1, "SLT-03", 54),
    ("GHE-03", "Nandini Pure Ghee 500 ml", "ghee", "Nandini", 305, 288, 1, None, 592),
    ("ATT-03", "Annapurna Atta 5 kg", "wheat flour (atta)", "Annapurna", 225, 213, 1, None, 436),
    ("DAL-03", "Fortune Toor Dal 1 kg", "toor dal", "Fortune", 175, 165, 1, None, 340),
    ("OIL-03", "Freedom Refined Sunflower Oil 1 L", "refined sunflower oil", "Freedom", 140, 134, 1, None, 272),
    ("RIC-03", "Fortune Everyday Basmati 1 kg", "basmati rice", "Fortune", 92, 85, 1, None, 178),
    ("MLK-03", "Nandini Toned Milk 1 L", "milk", "Nandini", 54, 52, 1, None, 105),
    ("SUG-03", "Parry's Refined Sugar 1 kg", "sugar", "Parry's", 50, 48, 1, None, 97),
    ("POT-03", "Local Loose Potato 1 kg", "potato", "Local Loose", 30, 25, 1, None, 58),
    ("ONI-03", "Local Loose Onion 1 kg", "onion", "Local Loose", 28, 23, 1, None, 54),
    ("SLT-03", "Annapurna Iodised Salt 1 kg", "iodised salt", "Annapurna", 22, 19, 1, None, 43),
]

# Imported from tokri_Database.xlsx (User_Data sheet): 10 seeded customers
# covering every negotiation/credit branch, including two deliberately
# incomplete profiles (C009 missing language, C010 missing almost
# everything) so the agent has to ask a question before it can price
# anything, instead of guessing.
# (external_id, name, phone, email, language, orders_90d, avg_basket, on_time_rate,
#  last_order_days, credit_limit, outstanding, data_status, missing_fields, agent_question)
CUSTOMERS = [
    ("C001", "Sunita Rao", "9000000001", "sunita.rao@example.com", "hi-IN", 9, 1450, 95.0, 6, 2000, 0, "COMPLETE", None, "No questions needed"),
    ("C002", "Meera Nair", "9000000002", "meera.nair@example.com", "kn-IN", 6, 1250, 93.0, 38, 1800, 200, "COMPLETE", None, "No questions needed"),
    ("C003", "Lakshmi Devi", "9000000003", "lakshmi.devi@example.com", "kn-IN", 7, 1100, 90.0, 15, 1500, 1500, "COMPLETE", None, "No questions needed"),
    ("C004", "Prakash Hegde", "9000000004", "prakash.hegde@example.com", "kn-IN", 4, 750, 85.0, 9, 1200, 200, "COMPLETE", None, "No questions needed"),
    ("C005", "Ramesh Kumar", "9000000005", "ramesh.kumar@example.com", "hi-IN", 3, 900, 80.0, 41, 1000, 300, "COMPLETE", None, "No questions needed"),
    ("C006", "Divya Shetty", "9000000006", "divya.shetty@example.com", "kn-IN", 3, 600, 65.0, 36, 800, 500, "COMPLETE", None, "No questions needed"),
    ("C007", "Anita Sharma", "9000000007", "anita.sharma@example.com", "hi-IN", 0, None, None, None, 0, 0, "COMPLETE", None, "No questions needed"),
    ("C008", "Vikram Joshi", "9000000008", "vikram.joshi@example.com", "en-IN", 1, 480, 50.0, 20, 500, 500, "COMPLETE", None, "No questions needed"),
    ("C009", "Farhan Ali", "9000000009", None, None, 1, 350, 100.0, 4, 0, 0, "INCOMPLETE", "language", "Ask: Which language are you comfortable in, Hindi, Kannada or English?"),
    ("C010", "Rohit Verma", "9000000010", None, "hi-IN", None, None, None, None, 0, 0, "INCOMPLETE", "orders_90d, on_time_rate, last_order_days, credit_limit_inr, outstanding_inr", "Ask: Have you ordered from us before, and is any payment pending? Until verified, treat as New (prepay only, 2% cap)."),
    # Added for live testing (not from the original spreadsheet).
    ("C011", "Madhumitha", "8088328962", None, "en-IN", 0, None, 100.0, None, 0, 0, "COMPLETE", None, "No questions needed"),
]


def seed() -> None:
    with get_conn() as conn:
        existing = conn.execute("SELECT COUNT(*) AS c FROM customers").fetchone()["c"]
        if existing > 0:
            return

        # Two passes: alt_sku can reference a product later in the list, and
        # SQLite checks foreign keys row-by-row, so a direct single-pass
        # insert would fail on the forward reference.
        conn.executemany(
            """
            INSERT INTO products (sku, name, category, brand, price, floor_price, in_stock, bundle_price_2)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[8]) for p in PRODUCTS],
        )
        conn.executemany(
            "UPDATE products SET alt_sku = ? WHERE sku = ?",
            [(p[7], p[0]) for p in PRODUCTS if p[7]],
        )

        for c in CUSTOMERS:
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
                """,
                (
                    external_id, name, phone, email, language, credit_limit, outstanding,
                    on_time_rate, orders_90d, avg_basket, last_order_at,
                    data_status, missing_fields, agent_question,
                ),
            )

    log_event("seed", detail=f"seeded {len(PRODUCTS)} products, {len(CUSTOMERS)} customers from tokri_Database.xlsx")
