def invoice_total(lines: list[int]) -> int:
    return _sum_lines(lines)


def _sum_lines(lines: list[int]) -> int:
    return sum(lines)
