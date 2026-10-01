def calculate_tax(amount: float, rate: float = 0.08) -> float:
    return round(amount * rate, 2)
