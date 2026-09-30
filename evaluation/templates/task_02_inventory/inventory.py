from typing import TypedDict


class Item(TypedDict):
    name: str
    price: float
    quantity: int


def total_cost(items: list[Item]) -> float:
    total = sum(item["price"] for item in items)
    return round(total, 2)