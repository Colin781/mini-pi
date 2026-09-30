import unittest

from calculator import add


class AddTest(unittest.TestCase):
    def test_positive_numbers(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_negative_number(self) -> None:
        self.assertEqual(add(-4, 1), -3)

    def test_zero(self) -> None:
        self.assertEqual(add(8, 0), 8)


if __name__ == "__main__":
    unittest.main()