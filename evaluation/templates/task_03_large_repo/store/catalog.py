from store.models import Product


def find_product(products: list[Product], sku: str) -> Product | None:
    return next((item for item in products if item.sku == sku), None)
