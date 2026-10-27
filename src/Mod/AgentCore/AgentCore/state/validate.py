# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal JSON Schema draft 2020-12 subset validator (stdlib only).

Covers the keywords state/schema.json uses: type, properties, required,
additionalProperties, patternProperties, items, enum, const, local $ref into
$defs, anyOf, oneOf, minimum, minItems, pattern. Unknown keywords are ignored.
The FreeCAD Python bundle ships no jsonschema, hence this module (first draft
by deepseek-v4.1-flash, reviewed and adjusted).
"""

import re


def _type_ok(inst, t):
    if t == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if t == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if t == "boolean":
        return isinstance(inst, bool)
    if t == "null":
        return inst is None
    if t == "object":
        return isinstance(inst, dict)
    if t == "array":
        return isinstance(inst, list)
    if t == "string":
        return isinstance(inst, str)
    return False


def _resolve(ref, root):
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return None
    return root.get("$defs", {}).get(ref[len("#/$defs/"):])


def _validate(inst, schema, path, root, errors):
    if schema is True:
        return
    if schema is False:
        errors.append(f"{path}: schema is false")
        return
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        target = _resolve(schema["$ref"], root)
        if target is None:
            errors.append(f"{path}: unresolved $ref {schema['$ref']!r}")
            return
        _validate(inst, target, path, root, errors)
    if "type" in schema:
        types = schema["type"]
        if isinstance(types, str):
            types = [types]
        if not any(_type_ok(inst, t) for t in types):
            errors.append(f"{path}: expected {' or '.join(types)}, got {type(inst).__name__}")
            return
    if "const" in schema and inst != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errors.append(f"{path}: value {inst!r} not in enum")
    if "minimum" in schema and _type_ok(inst, "number") and inst < schema["minimum"]:
        errors.append(f"{path}: value {inst} < minimum {schema['minimum']}")
    if "pattern" in schema and isinstance(inst, str) and re.search(schema["pattern"], inst) is None:
        errors.append(f"{path}: string does not match pattern {schema['pattern']!r}")
    if isinstance(inst, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in inst:
                errors.append(f"{path}: missing required property {name!r}")
        pat_props = schema.get("patternProperties", {})
        addl = schema.get("additionalProperties", True)
        for key, val in inst.items():
            sub = f"{path}/{key}"
            matched = False
            if key in props:
                matched = True
                _validate(val, props[key], sub, root, errors)
            for pat, pschema in pat_props.items():
                if re.search(pat, key):
                    matched = True
                    _validate(val, pschema, sub, root, errors)
            if not matched:
                if addl is False:
                    errors.append(f"{sub}: additional property not allowed")
                elif isinstance(addl, dict):
                    _validate(val, addl, sub, root, errors)
    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errors.append(f"{path}: array has {len(inst)} items, minimum {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(inst):
                _validate(item, schema["items"], f"{path}/{i}", root, errors)
    for key in ("anyOf", "oneOf"):
        if key not in schema:
            continue
        matches, first = 0, None
        for br in schema[key]:
            errs = []
            _validate(inst, br, path, root, errs)
            if errs:
                first = first or errs[0]
            else:
                matches += 1
                if key == "anyOf":
                    break
        if (key == "anyOf" and matches == 0) or (key == "oneOf" and matches != 1):
            errors.append(f"{path}: {key} matched {matches} branches (first error: {first})")


def validate(instance, schema):
    """Return a list of error strings; empty means valid."""
    errors = []
    _validate(instance, schema, "", schema, errors)
    return errors


def is_valid(instance, schema):
    """Return True if instance validates against schema."""
    return not validate(instance, schema)
