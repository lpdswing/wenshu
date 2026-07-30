from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict, fields
from types import MappingProxyType

import pytest

from coworker.content.images import (
    AssetManifest,
    AssetPlanError,
    CoverRequest,
    IllustrationPlan,
    IllustrationRequest,
    parse_asset_plan,
)


VALID_COVER_REQUEST = {
    "prompt": "冷色调的抽象知识网络，留出标题区域",
}


def illustration(index: int, **overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "heading": f"第 {index} 节",
        "prompt": f"第 {index} 节的概念示意图",
        "output_path": f"images/section-{index}.png",
    }
    request.update(overrides)
    return request


def test_defaults_and_zero_illustrations() -> None:
    plan = parse_asset_plan(VALID_COVER_REQUEST, [])

    assert plan == IllustrationPlan(
        cover=CoverRequest(
            prompt="冷色调的抽象知识网络，留出标题区域",
            output_path="cover.png",
            aspect_ratio="2.35:1",
            image_type="conceptual",
            palette="cool",
            rendering="digital",
            text_density="title-only",
            mood="bold",
        ),
        illustrations=(),
    )


def test_eight_illustrations_are_accepted_at_nine_image_total_limit() -> None:
    plan = parse_asset_plan(VALID_COVER_REQUEST, [illustration(i) for i in range(8)])

    assert len(plan.illustrations) == 8
    assert isinstance(plan.illustrations, tuple)
    assert plan.illustrations[0].aspect_ratio == "16:9"


def test_nine_illustrations_exceed_total_image_limit() -> None:
    with pytest.raises(AssetPlanError, match="9 images"):
        parse_asset_plan(VALID_COVER_REQUEST, [illustration(i) for i in range(9)])


def test_mapping_inputs_are_accepted_but_illustration_plan_must_be_a_list() -> None:
    plan = parse_asset_plan(MappingProxyType(VALID_COVER_REQUEST), [])
    assert plan.cover.output_path == "cover.png"

    with pytest.raises(AssetPlanError, match="list"):
        parse_asset_plan(VALID_COVER_REQUEST, (illustration(1),))


@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ([], []),
        ("cover", []),
        (VALID_COVER_REQUEST, {}),
        (VALID_COVER_REQUEST, ["illustration"]),
    ],
)
def test_non_mapping_or_list_containers_are_rejected(
    cover: object, illustrations: object
) -> None:
    with pytest.raises(AssetPlanError):
        parse_asset_plan(cover, illustrations)


@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ({**VALID_COVER_REQUEST, "unexpected": "value"}, []),
        (VALID_COVER_REQUEST, [{**illustration(1), "unexpected": "value"}]),
    ],
)
def test_unknown_fields_are_rejected(
    cover: object, illustrations: object
) -> None:
    with pytest.raises(AssetPlanError, match="unknown field"):
        parse_asset_plan(cover, illustrations)


@pytest.mark.parametrize(
    ("cover", "illustrations", "missing"),
    [
        ({}, [], "prompt"),
        (VALID_COVER_REQUEST, [{"prompt": "图", "output_path": "one.png"}], "heading"),
        (VALID_COVER_REQUEST, [{"heading": "一", "output_path": "one.png"}], "prompt"),
        (VALID_COVER_REQUEST, [{"heading": "一", "prompt": "图"}], "output_path"),
    ],
)
def test_required_fields_are_enforced(
    cover: object, illustrations: object, missing: str
) -> None:
    with pytest.raises(AssetPlanError, match=missing):
        parse_asset_plan(cover, illustrations)


@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ({"prompt": 1}, []),
        ({**VALID_COVER_REQUEST, "output_path": 1}, []),
        ({**VALID_COVER_REQUEST, "aspect_ratio": None}, []),
        ({**VALID_COVER_REQUEST, "palette": ["cool"]}, []),
        (VALID_COVER_REQUEST, [illustration(1, heading=1)]),
        (VALID_COVER_REQUEST, [illustration(1, prompt=False)]),
        (VALID_COVER_REQUEST, [illustration(1, output_path=None)]),
        (VALID_COVER_REQUEST, [illustration(1, aspect_ratio=16)]),
    ],
)
def test_declared_fields_require_strings(
    cover: object, illustrations: object
) -> None:
    with pytest.raises(AssetPlanError, match="string"):
        parse_asset_plan(cover, illustrations)


@pytest.mark.parametrize(
    ("cover", "illustrations"),
    [
        ({"prompt": ""}, []),
        ({"prompt": "  \t\n"}, []),
        (VALID_COVER_REQUEST, [illustration(1, prompt=" \n")]),
        (VALID_COVER_REQUEST, [illustration(1, heading="\t ")]),
    ],
)
def test_prompt_and_heading_must_not_be_blank(
    cover: object, illustrations: object
) -> None:
    with pytest.raises(AssetPlanError, match="blank"):
        parse_asset_plan(cover, illustrations)


@pytest.mark.parametrize(
    "aspect_ratio",
    ["", "bad", "16x9", "0:1", "1:0", "-1:1", "1:", "1:2:3", "NaN:1", " 16:9"],
)
def test_invalid_aspect_ratios_are_rejected(aspect_ratio: str) -> None:
    with pytest.raises(AssetPlanError, match="aspect_ratio"):
        parse_asset_plan(
            {**VALID_COVER_REQUEST, "aspect_ratio": aspect_ratio},
            [],
        )


def test_positive_decimal_aspect_ratio_and_relative_windows_path_are_accepted() -> None:
    plan = parse_asset_plan(
        {**VALID_COVER_REQUEST, "aspect_ratio": "2.35:1"},
        [illustration(1, output_path=r"images\figure.webp", aspect_ratio="4:3")],
    )

    assert plan.illustrations[0].output_path == "images/figure.webp"


@pytest.mark.parametrize(
    "output_path",
    [
        "/tmp/cover.png",
        "../cover.png",
        "images/../cover.png",
        "images/./cover.png",
        r"C:\temp\cover.png",
        "C:/temp/cover.png",
        r"C:cover.png",
        r"\\server\share\cover.png",
        "//server/share/cover.png",
        r"\rooted\cover.png",
        r"images\..\cover.png",
        r"images\.\cover.png",
        "cover",
        "cover.gif",
        "cover.png.exe",
        "images/\ncover.png",
        " ",
    ],
)
def test_unsafe_or_unsupported_output_paths_are_rejected(output_path: str) -> None:
    with pytest.raises(AssetPlanError, match="output_path"):
        parse_asset_plan({**VALID_COVER_REQUEST, "output_path": output_path}, [])


@pytest.mark.parametrize("extension", ["png", "PNG", "jpg", "JPG", "webp", "WEBP"])
def test_supported_extensions_are_case_insensitive(extension: str) -> None:
    plan = parse_asset_plan(
        {**VALID_COVER_REQUEST, "output_path": f"cover.{extension}"},
        [],
    )
    assert plan.cover.output_path == f"cover.{extension}"


def test_cover_and_illustration_outputs_use_windows_safe_duplicate_checking() -> None:
    with pytest.raises(AssetPlanError, match="duplicate"):
        parse_asset_plan(
            {**VALID_COVER_REQUEST, "output_path": "Images/Cover.PNG"},
            [illustration(1, output_path=r"images\cover.png")],
        )


def test_illustration_outputs_must_be_unique() -> None:
    with pytest.raises(AssetPlanError, match="duplicate"):
        parse_asset_plan(
            VALID_COVER_REQUEST,
            [
                illustration(1, output_path="images/Diagram.webp"),
                illustration(2, output_path=r"IMAGES\diagram.WEBP"),
            ],
        )


def test_plan_models_are_frozen_and_normalize_sequences_to_tuples() -> None:
    cover = CoverRequest(prompt="封面")
    request = IllustrationRequest(heading="第一节", prompt="配图", output_path="one.png")
    plan = IllustrationPlan(cover=cover, illustrations=[request])  # type: ignore[arg-type]

    assert plan.illustrations == (request,)
    with pytest.raises(FrozenInstanceError):
        plan.cover = CoverRequest(prompt="另一个封面")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.prompt = "修改"  # type: ignore[misc]


def test_asset_manifest_is_frozen_serializable_and_contains_no_prompt_field() -> None:
    manifest = AssetManifest(
        reviewed_hash="reviewed",
        plan_hash="plan",
        provider="openai",
        model="gpt-image-2",
        assets=[{"output_path": "cover.png", "sha256": "abc123"}],  # type: ignore[arg-type]
    )

    assert manifest.assets == ({"output_path": "cover.png", "sha256": "abc123"},)
    assert {field.name for field in fields(AssetManifest)} == {
        "reviewed_hash",
        "plan_hash",
        "provider",
        "model",
        "assets",
    }
    assert "prompt" not in json.dumps(asdict(manifest))
    with pytest.raises(FrozenInstanceError):
        manifest.provider = "other"  # type: ignore[misc]


def test_asset_manifest_rejects_prompt_bearing_asset_entries() -> None:
    with pytest.raises(AssetPlanError, match="unknown field"):
        AssetManifest(
            reviewed_hash="reviewed",
            plan_hash="plan",
            provider="openai",
            model="gpt-image-2",
            assets=[
                {
                    "output_path": "cover.png",
                    "sha256": "abc123",
                    "prompt": "sensitive source material",
                }
            ],  # type: ignore[arg-type]
        )


def test_image_plan_interface_is_exported_by_content_package() -> None:
    import coworker.content as content

    assert content.AssetManifest is AssetManifest
    assert content.AssetPlanError is AssetPlanError
    assert content.CoverRequest is CoverRequest
    assert content.IllustrationPlan is IllustrationPlan
    assert content.IllustrationRequest is IllustrationRequest
    assert content.parse_asset_plan is parse_asset_plan
