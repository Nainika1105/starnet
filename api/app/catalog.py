"""Redemption catalogue. STUB: a static list standing in for real partner
coupon and municipal-credit integrations."""
from decimal import Decimal

from app.schemas import CatalogItem

CATALOG: list[CatalogItem] = [
    CatalogItem(
        sku="BUS-PASS-1D",
        name="One-day city bus pass",
        cost=Decimal("40.0000"),
        description="Municipal transit credit, valid 24 hours.",
    ),
    CatalogItem(
        sku="WATER-BILL-50",
        name="Rs. 50 off your water bill",
        cost=Decimal("60.0000"),
        description="Applied to your next municipal water bill.",
    ),
    CatalogItem(
        sku="CAFE-COFFEE",
        name="Partner cafe coffee",
        cost=Decimal("25.0000"),
        description="One coffee at any partner cafe.",
    ),
    CatalogItem(
        sku="BADGE-CIVIC",
        name="Civic Guardian badge",
        cost=Decimal("10.0000"),
        description="A profile badge. Costs little, says a lot.",
    ),
]

BY_SKU = {item.sku: item for item in CATALOG}
