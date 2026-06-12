def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ValueError("b cannot be zero")
    return a / b


class Calculator:
    def multiply(self, a, b):
        return a * b

    def is_even(self, n):
        return n % 2 == 0
