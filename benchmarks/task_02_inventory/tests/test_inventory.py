import unittest

from inventory import total_cost


class TotalCostTest(unittest.TestCase):
    def test_empty_cart(self) -> None:
        self.assertEqual(total_cost([]), 0)

    def test_single_item(self) -> None:
        items = [
            {
                "name": "keyboard",
                "price": 299.0,
                "quantity": 1,
            }
        ]

        self.assertEqual(total_cost(items), 299.0)

    def test_multiple_quantities(self) -> None:
        items = [
            {
                "name": "notebook",
                "price": 12.5,
                "quantity": 3,
            },
            {
                "name": "pen",
                "price": 4.0,
                "quantity": 2,
            },
        ]

        self.assertEqual(total_cost(items), 45.5)

    def test_decimal_result_is_rounded(self) -> None:
        items = [
            {
                "name": "component",
                "price": 1.005,
                "quantity": 2,
            }
        ]

        self.assertEqual(total_cost(items), 2.01)


if __name__ == "__main__":
    unittest.main()