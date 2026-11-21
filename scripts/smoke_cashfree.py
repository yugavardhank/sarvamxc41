"""Hits the real Cashfree sandbox once, to sanity-check credentials outside of
the app. Requires MOCK_CASHFREE=0 and real CASHFREE_APP_ID/CASHFREE_SECRET in
.env. Run with: python scripts/smoke_cashfree.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import cashfree


def main():
    link = cashfree.create_payment_link(amount=221, customer_phone="+919000000001", purpose="Tokri smoke test")
    print("payment link:", link)


if __name__ == "__main__":
    main()
