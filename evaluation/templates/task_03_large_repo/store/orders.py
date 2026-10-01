from dataclasses import dataclass

from store.pricing import calculate_discount
from store.taxes import calculate_tax


@dataclass(frozen=True)
class Order:
    subtotal: float
    customer_tier: str = "regular"

    def total(self) -> float:
        discount = calculate_discount(self.subtotal, self.customer_tier)
        return round(self.subtotal - discount + calculate_tax(self.subtotal), 2)
