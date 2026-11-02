import re
from typing import Any

import pytest
from pydantic import ValidationError

from hyperwiki.domain.work import Component, WorkFingerprint, fingerprint_id

BASE: dict[str, Any] = {
    "operation": "extract.text",
    "operation_version": 1,
    "inputs": {"attachment_version": "v1", "sha256": "a" * 64},
    "extractor": {"name": "pdf-extractor", "version": "1.0.0"},
    "model": {"name": "granite-embedding", "version": "d6cf", "runtime": "onnxruntime@1.31.1"},
    "schema_version": 1,
    "config": {"chunk_chars": 800, "language": "en", "ocr": False, "overlap": 0.1},
}


def make(**changes: Any) -> WorkFingerprint:
    return WorkFingerprint.model_validate({**BASE, **changes})


def test_fingerprint_id_format() -> None:
    assert re.fullmatch(r"fp_[0-9a-f]{32}", fingerprint_id(make()))


def test_fingerprint_is_stable_across_key_order() -> None:
    reordered = make(
        inputs={"sha256": "a" * 64, "attachment_version": "v1"},
        config={"overlap": 0.1, "ocr": False, "language": "en", "chunk_chars": 800},
    )
    assert fingerprint_id(reordered) == fingerprint_id(make())


VARIANTS: dict[str, dict[str, Any]] = {
    "operation": {"operation": "extract.tables"},
    "operation_version": {"operation_version": 2},
    "inputs": {"inputs": {"attachment_version": "v2", "sha256": "a" * 64}},
    "extractor_version": {"extractor": {"name": "pdf-extractor", "version": "1.0.1"}},
    "extractor_removed": {"extractor": None},
    "model_revision": {
        "model": {"name": "granite-embedding", "version": "e000", "runtime": "onnxruntime@1.31.1"}
    },
    "model_runtime": {
        "model": {"name": "granite-embedding", "version": "d6cf", "runtime": "torch@2.12.0"}
    },
    "schema_version": {"schema_version": 2},
    "config_value": {"config": {**BASE["config"], "chunk_chars": 900}},
    "config_type": {"config": {**BASE["config"], "chunk_chars": "800"}},
    "config_key": {"config": {**BASE["config"], "prompt_version": "p1"}},
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_fingerprint_changes_with_each_component(name: str) -> None:
    assert fingerprint_id(make(**VARIANTS[name])) != fingerprint_id(make())


def test_all_variants_are_distinct() -> None:
    ids = {fingerprint_id(make(**changes)) for changes in VARIANTS.values()}
    assert len(ids) == len(VARIANTS)


@pytest.mark.parametrize("operation", ["", "Extract", "extract..text", "1extract", "extract-text"])
def test_fingerprint_rejects_bad_operation(operation: str) -> None:
    with pytest.raises(ValidationError):
        make(operation=operation)


def test_fingerprint_rejects_empty_inputs_and_values() -> None:
    with pytest.raises(ValidationError):
        make(inputs={})
    with pytest.raises(ValidationError):
        make(inputs={"attachment_version": ""})
    with pytest.raises(ValidationError):
        make(operation_version=0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_fingerprint_rejects_non_finite_config(value: float) -> None:
    with pytest.raises(ValidationError):
        make(config={"threshold": value})


def test_chinese_config_values_round_trip() -> None:
    original = make(config={"language": "中文", "term": "注意力机制"})
    restored = WorkFingerprint.model_validate_json(original.model_dump_json())
    assert restored == original
    assert fingerprint_id(restored) == fingerprint_id(original)


def test_component_requires_name_and_version() -> None:
    with pytest.raises(ValidationError):
        Component(name="", version="1")
    with pytest.raises(ValidationError):
        Component(name="x", version="")
