def order_count(order_ids: list[str]) -> int:
    return len(set(order_ids))
