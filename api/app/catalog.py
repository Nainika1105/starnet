"""Redemption catalogue.

STUB: a static list standing in for partner coupon and municipal-credit
integrations. Nothing here is real, and nothing here is bought: redeeming
deducts points and records the choice, and no voucher, credit or payment is
issued. The descriptions say so, because "Rs. 50 off your water bill" reads
like real value and a demo audience should not have to guess.

See app/fulfilment.py for why that is enforced rather than merely intended.
"""
from decimal import Decimal

from app.schemas import CatalogItem

CATALOG: list[CatalogItem] = [
    CatalogItem(
        sku="BUS-PASS-1D",
        name="One-day city bus pass",
        cost=Decimal("40.0000"),
        description="Municipal transit credit, valid 24 hours. Simulated: nothing is issued.",
    ),
    CatalogItem(
        sku="WATER-BILL-50",
        name="Rs. 50 off your water bill",
        cost=Decimal("60.0000"),
        description="Applied to your next municipal water bill. Simulated: no credit is applied.",
    ),
    CatalogItem(
        sku="CAFE-COFFEE",
        name="Partner cafe coffee",
        cost=Decimal("25.0000"),
        description="One coffee at any partner cafe. Simulated: no voucher is issued.",
    ),
    CatalogItem(
        sku="BADGE-CIVIC",
        name="Civic Guardian badge",
        cost=Decimal("10.0000"),
        description="A profile badge. Costs little, says a lot. The only item that is not pretending.",
    ),
]

BY_SKU = {item.sku: item for item in CATALOG}
