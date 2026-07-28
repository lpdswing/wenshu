from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ProductProfile:
    id: str
    name: str
    display_name: str
    default_persona: str
    visible_connectors: frozenset[str]
    features: dict[str, bool]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["visible_connectors"] = sorted(self.visible_connectors)
        return data


WENSHU_PROFILE = ProductProfile(
    id="wenshu",
    name="文枢",
    display_name="文枢 WenShu",
    default_persona="cowork",
    visible_connectors=frozenset({"browser", "wechat_official"}),
    features={
        "cloud": False,
        "gallery": False,
        "managed_oauth": False,
        "relay": False,
        "updater": False,
    },
)


def current_product() -> ProductProfile:
    return WENSHU_PROFILE
