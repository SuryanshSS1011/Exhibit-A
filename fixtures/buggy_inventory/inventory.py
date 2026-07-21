"""Small inventory helpers used by the Exhibit A live demo."""


def stock_for(items: list[dict[str, object]], sku: str) -> int:
    """Return the available quantity for a SKU, or zero when it is absent."""
    quantities = {str(item["sku"]): int(item["quantity"]) for item in items}
    return quantities[sku]
