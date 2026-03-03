from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict


class SchemaValidationError(ValueError):
    pass


try:
    from jsonschema import Draft202012Validator  # type: ignore
except Exception:  # pragma: no cover
    Draft202012Validator = None  # type: ignore


_TYPE_MAP = {
    "object": dict,
    "string": str,
    "integer": int,
    "array": list,
    "boolean": bool,
    "number": (int, float),
}

def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_none(x) for x in value]
    return value



def load_schema(name: str) -> Dict[str, Any]:
    text = resources.files("robotos.schemas").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


def validate(instance: Dict[str, Any], schema_name: str) -> None:
    schema = load_schema(schema_name)
    instance = _strip_none(instance)
    if Draft202012Validator is not None:
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
        if errors:
            err = errors[0]
            path = ".".join(str(p) for p in err.path) or "<root>"
            raise SchemaValidationError(f"{schema_name} validation failed at {path}: {err.message}")
        return
    _validate_object(instance, schema, path="<root>")


def _validate_object(instance: Dict[str, Any], schema: Dict[str, Any], path: str) -> None:
    required = schema.get("required", [])
    for key in required:
        if key not in instance:
            raise SchemaValidationError(f"{path} missing required field: {key}")

    props = schema.get("properties", {})
    for key, rule in props.items():
        if key not in instance:
            continue
        val = instance[key]
        if val is None:
            continue
        _validate_rule(val, rule, f"{path}.{key}")


def _validate_rule(val: Any, rule: Dict[str, Any], path: str) -> None:
    typ = rule.get("type")
    if typ:
        py_type = _TYPE_MAP.get(typ)
        if py_type and not isinstance(val, py_type):
            raise SchemaValidationError(f"{path} expected {typ}, got {type(val).__name__}")

    if "enum" in rule and val not in rule["enum"]:
        raise SchemaValidationError(f"{path} must be one of {rule['enum']}, got {val}")

    if isinstance(val, dict):
        nested = {"properties": rule.get("properties", {}), "required": rule.get("required", [])}
        _validate_object(val, nested, path)

    if isinstance(val, list) and "items" in rule:
        item_rule = rule["items"]
        for i, item in enumerate(val):
            if isinstance(item_rule, dict):
                _validate_rule(item, item_rule, f"{path}[{i}]")
