"""The models must stay generable from the frozen V1 contract.

Mirrors ``nbq-engine/scripts/openapi_check_models.py``: every named schema of
``openapi/nbq-v1.openapi.yaml`` must have a model, and every documented example
— request bodies, responses, shared ``components/responses``, and the
``components/examples`` they reference — must be accepted by the model the
adjacent ``$ref`` names. An example the SDK refuses means the contract and the
SDK have drifted apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, TypeAdapter
from zelinqa import models

SCHEMA_PREFIX = "#/components/schemas/"
EXAMPLE_PREFIX = "#/components/examples/"

#: Deprecated compatibility routes are absent from the V1 SDK surface.
EXCLUDED_SCHEMA_PREFIXES = ("Legacy",)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "openapi" / "nbq-v1.openapi.yaml"

#: ``(location, schema name, example value)``.
Example = tuple[str, str, Any]


def _load_spec() -> dict[str, Any]:
    document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{SPEC_PATH} is not a YAML mapping"
    return document


SPEC: dict[str, Any] = _load_spec()


def _resolve_example(document: dict[str, Any], node: dict[str, Any]) -> Any:
    ref = node.get("$ref")
    if ref is None:
        return node.get("value")
    assert ref.startswith(EXAMPLE_PREFIX), f"unsupported example reference: {ref}"
    name = ref.removeprefix(EXAMPLE_PREFIX)
    shared = document["components"]["examples"]
    assert name in shared, f"dangling example reference: {ref}"
    return shared[name].get("value")


def _iter_examples(document: dict[str, Any], node: Any, location: str) -> Iterator[Example]:
    """Walk the whole document, yielding every ``content`` with a named schema."""

    if isinstance(node, list):
        for index, child in enumerate(node):
            yield from _iter_examples(document, child, f"{location}/{index}")
        return
    if not isinstance(node, dict):
        return

    content = node.get("content")
    if isinstance(content, dict):
        for media, body in content.items():
            if not isinstance(body, dict):
                continue
            schema = body.get("schema")
            ref = schema.get("$ref") if isinstance(schema, dict) else None
            if not isinstance(ref, str) or not ref.startswith(SCHEMA_PREFIX):
                continue
            name = ref.removeprefix(SCHEMA_PREFIX)
            where = f"{location}/content/{media}"
            if isinstance(body.get("examples"), dict):
                for label, example in body["examples"].items():
                    yield (
                        f"{where}/examples/{label}",
                        name,
                        _resolve_example(document, example),
                    )
            elif "example" in body:
                yield f"{where}/example", name, body["example"]

    for key, child in node.items():
        yield from _iter_examples(document, child, f"{location}/{key}")


EXAMPLES: list[Example] = list(_iter_examples(SPEC, SPEC, ""))
SCHEMA_NAMES: list[str] = sorted(SPEC["components"]["schemas"])
MODELLED_SCHEMA_NAMES = [
    name for name in SCHEMA_NAMES if not name.startswith(EXCLUDED_SCHEMA_PREFIXES)
]


def _validate(name: str, value: Any) -> None:
    target = getattr(models, name, None)
    assert target is not None, f"nbq.models has no {name}"
    if isinstance(target, type) and issubclass(target, BaseModel):
        target.model_validate(value)
    else:
        # Discriminated unions and enumerations are type aliases, not classes.
        TypeAdapter(target).validate_python(value)


def test_spec_snapshot_is_readable() -> None:
    assert SPEC["info"]["version"] == "1.0.0"
    assert SPEC["openapi"].startswith("3.1")


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "python/README.md",
        "typescript/README.md",
        "PUBLISHING.md",
        "openapi/nbq-v1.openapi.yaml",
        "typescript/src/types.ts",
        "typescript/src/generated/openapi.d.ts",
    ],
)
def test_public_documentation_describes_current_api(relative_path: str) -> None:
    text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    assert not re.search(r"\bv?0\.9\b", text, re.IGNORECASE), relative_path


def test_every_named_schema_has_a_model() -> None:
    missing = [name for name in MODELLED_SCHEMA_NAMES if not hasattr(models, name)]
    assert not missing, f"no model for: {', '.join(missing)}"


@pytest.mark.parametrize("name", MODELLED_SCHEMA_NAMES)
def test_named_schema_is_exposed(name: str) -> None:
    target = getattr(models, name, None)
    assert target is not None, f"nbq.models has no {name}"
    schema = SPEC["components"]["schemas"][name]
    if schema.get("type") == "string" and "enum" in schema:
        # A closed enumeration is a Literal alias, never a class.
        assert not isinstance(target, type), f"{name} should be a Literal alias"


def test_legacy_schemas_are_not_exposed() -> None:
    legacy = [name for name in SCHEMA_NAMES if name.startswith(EXCLUDED_SCHEMA_PREFIXES)]
    assert legacy, "the snapshot should still document deprecated compatibility routes"
    leaked = [name for name in legacy if hasattr(models, name)]
    assert not leaked, f"the V1 SDK must not expose: {', '.join(leaked)}"


def test_the_contract_documents_examples() -> None:
    assert len(EXAMPLES) >= 30, f"only {len(EXAMPLES)} examples found, the walker is wrong"


@pytest.mark.parametrize(
    ("location", "name", "value"),
    EXAMPLES,
    ids=[location for location, _, _ in EXAMPLES],
)
def test_example_validates_against_its_schema(location: str, name: str, value: Any) -> None:
    assert value is not None, f"{location}: example has no value"
    _validate(name, value)


@pytest.mark.parametrize("label", sorted(SPEC["components"]["examples"]))
def test_shared_example_is_reached_by_the_walker(label: str) -> None:
    referenced = {
        name
        for name, node in SPEC["components"]["examples"].items()
        if any(value is node.get("value") for _, _, value in EXAMPLES)
    }
    assert label in referenced, f"components/examples/{label} is never validated"


def test_error_envelope_examples_cover_every_error_code() -> None:
    seen = {
        value["code"]
        for _, name, value in EXAMPLES
        if name == "ErrorEnvelope" and isinstance(value, dict)
    }
    documented = set(SPEC["components"]["schemas"]["ErrorCode"]["enum"])
    assert seen == documented, f"error codes without an example: {sorted(documented - seen)}"
