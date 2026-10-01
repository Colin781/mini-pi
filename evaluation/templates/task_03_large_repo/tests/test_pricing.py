import unittest

from store.pricing import calculate_discount


class PricingTest(unittest.TestCase):
    def test_premium_discount_is_ten_percent(self):
        self.assertEqual(calculate_discount(120.0, "premium"), 12.0)


if __name__ == "__main__":
    unittest.main()
