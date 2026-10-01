def shipping_cost(weight_kg: float) -> float:
    return 0.0 if weight_kg <= 0 else round(4.5 + weight_kg * 0.8, 2)
