from users import active_names


def test_active_names_contract():
    assert active_names([]) == []
    assert active_names(
        [
            {"name": "Ada", "active": True},
            {"name": "Grace", "active": False},
            {"name": "Linus", "active": True},
        ]
    ) == ["Ada", "Linus"]
