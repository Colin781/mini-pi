DISCOUNT_RATES = {
    "regular": 0.0,
    "premium": 0.10,
}


def calculate_discount(subtotal: float, customer_tier: str) -> float:
    rate = DISCOUNT_RATES.get(customer_tier, 0.0)
    return round(subtotal * rate / 100, 2)
