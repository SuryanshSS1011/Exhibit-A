from invoice import invoice_total


def test_invoice_contract():
    assert invoice_total([]) == 0
    assert invoice_total([4]) == 4
    assert invoice_total([4, -1, 7]) == 10
