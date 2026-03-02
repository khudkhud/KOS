from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict


class SchemaValidationError(ValueError):
    pass


_TYPE_MAP = {
    "object": dict,
    "string": str,
    "integer": int,
    "array": list,
}


def load_schema(name: str) -> Dict[str, Any]:
    text = resources.files("robotos.schemas").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


def validate(instance: Dict[str, Any], schema_name: str) -> None:
    schema = load_schema(schema_name)
    _validate_object(instance, schema)


def _validate_object(instance: Dict[str, Any], schema: Dict[str, Any]) -> None:
    required = schema.get("required", [])
    for key in required:
        if key not in instance:
            raise SchemaValidationError(f"missing required field: {key}")

    props = schema.get("properties", {})
    for key, rule in props.items():
        if key not in instance:
            continue
        val = instance[key]
        if val is None:
            continue
        typ = rule.get("type")
        if typ:
            py_type = _TYPE_MAP.get(typ)
            if py_type and not isinstance(val, py_type):
                raise SchemaValidationError(f"field {key} expected {typ}, got {type(val).__name__}")
        if "enum" in rule and val not in rule["enum"]:
            raise SchemaValidationError(f"field {key} must be one of {rule['enum']}, got {val}")
