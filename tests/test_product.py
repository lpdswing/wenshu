from coworker.product import current_product


def test_wenshu_product_defaults():
    p = current_product()
    assert p.id == "wenshu"
    assert p.name == "文枢"
    assert p.display_name == "文枢 WenShu"
    assert p.default_persona == "cowork"
    assert p.features == {
        "cloud": False,
        "gallery": False,
        "managed_oauth": False,
        "relay": False,
        "updater": False,
    }
    assert p.visible_connectors == frozenset({"browser", "wechat_official"})
