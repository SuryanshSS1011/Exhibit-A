from slug import normalize_label


def test_normalize_contract():
    assert normalize_label("") == ""
    assert normalize_label("  Hello   World  ") == "hello-world"
    assert normalize_label("Already-Normal") == "already-normal"
