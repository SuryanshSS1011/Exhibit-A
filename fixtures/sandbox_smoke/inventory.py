"""A trivial module for the sandbox smoke test to exercise inside a container."""


def stock_for(catalog: dict[str, int], sku: str) -> int:
    return catalog.get(sku, 0)
