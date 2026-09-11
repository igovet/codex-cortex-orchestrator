"""Small calculator whose behavior is already correct."""


def add(left, right):
    return left + right


def divide(left, right):
    if right == 0:
        raise ZeroDivisionError("right operand must not be zero")
    return left / right
