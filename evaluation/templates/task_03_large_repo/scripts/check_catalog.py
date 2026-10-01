from store.catalog import find_product


def main() -> int:
    return 0 if find_product([], "missing") is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
