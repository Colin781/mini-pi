def format_currency(amount: float, symbol: str = "¥") -> str:
    return f"{symbol}{amount:.2f}"
