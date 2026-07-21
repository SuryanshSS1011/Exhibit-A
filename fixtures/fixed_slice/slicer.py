"""The fixed counterpart to fixtures/buggy_slice — the 'base/fixed' state.

Used to prove the pass side of the flip check: the same test that fails on the
buggy checkout must pass here.
"""


def last_n(items, n):
    return items[-n:] if n else []
