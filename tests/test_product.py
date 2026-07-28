import pytest

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


def test_wenshu_feature_flags_are_immutable():
    p = current_product()

    with pytest.raises(TypeError):
        p.features["cloud"] = True

    serialized = p.to_dict()
    assert isinstance(serialized["features"], dict)
    serialized["features"]["cloud"] = True
    assert p.features["cloud"] is False
