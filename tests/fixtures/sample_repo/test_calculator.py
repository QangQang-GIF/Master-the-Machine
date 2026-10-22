"""Tests for the calculator module."""
from calculator import calculate_discount


def test_no_discount_below_threshold():
    assert calculate_discount(50.0) == 50.0


def test_discount_above_threshold():
    assert calculate_discount(200.0) == 180.0


def test_discount_exact_threshold():
    """MVS-1: Discount must apply when price equals threshold."""
    assert calculate_discount(100.0) == 90.0
