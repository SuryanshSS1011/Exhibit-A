def invoice_total(lines: list[int]) -> int:
    return _accumulate_lines(lines)


def _accumulate_lines(lines: list[int]) -> int:
    return sum(lines)
