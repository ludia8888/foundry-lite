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
    "description": (
        "Extract the user's stated business facts before calling the plan tool. Include every explicitly "
        "mentioned record, person, workflow step, action, rule, and evidence item. Keep display names in the "
        "user's language; do not guess missing facts. Empty arrays mean a genuine missing business detail."
    ),
    "additionalProperties": False,
    "properties": {
        "actors": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 12,
            "description": "Business-facing names of every person or role the user named, in the user's language.",
        },
        "records": {
            "type": "array",
            "description": (
                "Every business record or case explicitly named by the user; do not leave this empty when known."
            ),
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name"],
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "description": "Customer-facing record name in the user's language, not an API name.",
                    },
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
        "lifecycleStates": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 16,
            "description": (
                "Business-facing workflow labels in the user's language. Action state references must match exactly."
            ),
        },
        "actions": {
            "type": "array",
            "description": (
                "Every work step or button explicitly requested by the user; preserve the user's business wording."
            ),
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "toState"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "apiName": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9]{0,63}$"},
                    "description": {"type": "string", "maxLength": 500},
                    "fromStates": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                        "description": "Exact strings from lifecycleStates; do not invent a new code.",
                    },
                    "toState": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                        "description": "Exact string from lifecycleStates; do not invent a new code.",
                    },
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
            "description": "Every stated must, must-not, approval, deadline, exception, or review rule.",
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
