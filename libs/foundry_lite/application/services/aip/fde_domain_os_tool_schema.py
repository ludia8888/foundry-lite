"""Closed JSON Schema fragments for non-developer Domain OS planning."""

from __future__ import annotations

_TEXT_LIST_20 = {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 20}
_POLICY_PROPERTY = {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"}
_POLICY_VALUE_OPERATORS = [
    "eq",
    "neq",
    "in",
    "notIn",
    "lt",
    "lte",
    "gt",
    "gte",
    "contains",
    "startsWith",
    "matches",
]
_POLICY_CONDITION_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["propertyApiName", "operator", "value"],
            "properties": {
                "propertyApiName": _POLICY_PROPERTY,
                "operator": {"type": "string", "enum": _POLICY_VALUE_OPERATORS},
                "value": {},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["propertyApiName", "operator"],
            "properties": {
                "propertyApiName": _POLICY_PROPERTY,
                "operator": {"type": "string", "const": "exists"},
            },
        },
    ]
}

DOMAIN_BRIEF_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actors": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 12},
        "records": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "apiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "description": {"type": "string", "maxLength": 500},
                    "fields": {
                        "type": "array",
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name"],
                            "properties": {
                                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                                "apiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                                "type": {
                                    "type": "string",
                                    "enum": ["string", "integer", "float", "boolean", "date", "timestamp"],
                                },
                                "required": {"type": "boolean"},
                                "description": {"type": "string", "maxLength": 300},
                            },
                        },
                    },
                },
            },
        },
        "lifecycleStates": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 16},
        "actions": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "toState"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "apiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "description": {"type": "string", "maxLength": 500},
                    "fromStates": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    "toState": {"type": "string", "minLength": 1, "maxLength": 120},
                    "requiredInformation": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 12,
                    },
                    "allowedActors": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 12,
                    },
                    "requiresApproval": {"type": "boolean"},
                },
            },
        },
        "functions": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "recordApiName", "aggregation", "allowedActors"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "apiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "description": {"type": "string", "maxLength": 500},
                    "recordApiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "aggregation": {"type": "string", "enum": ["count", "sum", "avg", "min", "max"]},
                    "propertyApiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "allowedActors": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 12,
                    },
                    "filters": {
                        "type": "array",
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["propertyApiName", "operator", "value"],
                            "properties": {
                                "propertyApiName": {
                                    "type": "string",
                                    "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$",
                                },
                                "operator": {
                                    "type": "string",
                                    "enum": ["eq", "in", "gt", "gte", "lt", "lte", "contains"],
                                },
                                "value": {},
                            },
                        },
                    },
                },
            },
        },
        "policies": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "statement"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 160},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "enforcement": {"type": "string", "enum": ["blocking", "warning", "manual_review"]},
                    "evidence": {"type": "string", "maxLength": 500},
                    "appliesToActions": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                        "maxItems": 20,
                    },
                    "conditions": {
                        "type": "array",
                        "maxItems": 12,
                        "items": _POLICY_CONDITION_SCHEMA,
                    },
                },
            },
        },
        "evidence": _TEXT_LIST_20,
        "integrations": _TEXT_LIST_20,
        "successMeasures": _TEXT_LIST_20,
    },
}

__all__ = ["DOMAIN_BRIEF_SCHEMA"]
