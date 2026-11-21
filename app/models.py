from typing import Literal, Optional

from pydantic import BaseModel

OrderStatus = Literal[
    "REQUESTED",
    "BASKET_DRAFT",
    "NEGOTIATING",
    "AWAITING_PAYMENT",
    "CONFIRMED",
    "ON_ACCOUNT",
    "EXPIRED",
    "CANCELLED",
]


class RequestedItem(BaseModel):
    name: str
    qty: Optional[int] = None
    brand: Optional[str] = None


class ItemExtraction(BaseModel):
    items: list[RequestedItem]
    budget_hint: Optional[float] = None
    language: str


class MatchedItem(BaseModel):
    requested_text: str
    matched_sku: Optional[str] = None
    product_name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    qty: int
    unit_price: int
    line_total: int
