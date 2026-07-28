from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class ProductProfile:
    id: str
    name: str
    display_name: str
    default_persona: str
    visible_connectors: frozenset[str]
    features: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "default_persona": self.default_persona,
            "visible_connectors": sorted(self.visible_connectors),
            "features": dict(self.features),
        }


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
