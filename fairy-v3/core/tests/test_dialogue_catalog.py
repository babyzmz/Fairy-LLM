from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from fairy_core.persona import (
    DialogueCatalog,
    DialogueSource,
    DialogueTrigger,
    load_default_dialogue_catalog,
)


def test_catalog_has_reviewed_semantic_parity_for_supported_locales() -> None:
    catalog = load_default_dialogue_catalog()

    assert catalog.locales == ("en", "zh-CN")
    assert len(catalog.for_locale("zh-CN")) >= 64
    assert len(catalog.for_locale("en")) >= 64
    assert {item.semantic_id for item in catalog.for_locale("zh-CN")} == {
        item.semantic_id for item in catalog.for_locale("en")
    }
    assert {item.source.value for item in catalog.items} <= {
        "protected",
        "authored_original",
    }

    triggers = Counter(item.trigger for item in catalog.for_locale("zh-CN"))
    for trigger in DialogueTrigger:
        assert triggers[trigger] > 0


def test_catalog_contains_only_bounded_reviewed_lines() -> None:
    catalog = load_default_dialogue_catalog()

    for item in catalog.items:
        assert item.source in {DialogueSource.PROTECTED, DialogueSource.AUTHORED_ORIGINAL}
        assert 1 <= item.sentence_count <= 3
        assert len(item.text) <= 280
        assert not item.risk_tags


def test_daily_startup_utterance_is_protected_and_exact() -> None:
    catalog = load_default_dialogue_catalog()
    item = catalog.get("startup.daily.01", "zh-CN")

    assert item.source is DialogueSource.PROTECTED
    assert item.text == (
        "系统启动完成——我是Ⅲ型总序式集成泛用人工智能\uff0c开发代号 Fairy。你好\uff0c主人。"
    )
    assert item.tts_allowed is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source", DialogueSource.GENERATED_ORIGINAL),
        ("text", "I read your clipboard."),
        ("text", "Battery is {unknown_fact}."),
        ("required_facts", ("unknown_fact",)),
    ),
)
def test_catalog_rejects_unreviewed_sources_and_unbound_capability_claims(
    field: str,
    value: object,
) -> None:
    original = load_default_dialogue_catalog().get("idle.short.01", "en")

    with pytest.raises(ValueError):
        DialogueCatalog((replace(original, **{field: value}),))
