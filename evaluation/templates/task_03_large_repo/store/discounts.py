def coupon_discount(subtotal: float, percent: int) -> float:
    return round(subtotal * max(0, min(percent, 100)) / 100, 2)
