import unittest

from store.inventory import available


class InventoryTest(unittest.TestCase):
    def test_available_stock(self):
        self.assertTrue(available(5, 3))


if __name__ == "__main__":
    unittest.main()
