"""A deliberately buggy fixture repo for exercising the Evidence Engine.

`last_n` is meant to return the last N items but the slice is off-by-one, so it
drops the final element. A real Detective run should reproduce this into a
fail-to-pass test.
"""


def last_n(items, n):
    # BUG: should be items[-n:]; this drops the last element.
    return items[-n:-1]
