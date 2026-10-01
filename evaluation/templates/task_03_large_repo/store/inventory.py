def available(stock: int, requested: int) -> bool:
    return requested > 0 and stock >= requested
