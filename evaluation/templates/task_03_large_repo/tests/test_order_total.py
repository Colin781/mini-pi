import unittest

from store.orders import Order


class OrderTotalTest(unittest.TestCase):
    def test_premium_discount_is_applied_before_tax(self):
        self.assertEqual(Order(120.0, "premium").total(), 116.64)


if __name__ == "__main__":
    unittest.main()
