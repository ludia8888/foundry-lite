"""Application service helpers for transform partition pushdown workflows."""

from __future__ import annotations

import re

from foundry_lite.application.primitives import INPUT_PATTERN

_SQL_KEYWORDS = frozenset(
    {
        "cross",
        "full",
        "group",
        "inner",
        "join",
        "left",
        "limit",
        "on",
        "order",
        "right",
        "union",
        "where",
    }
)
_WHERE_PATTERN = re.compile(
    r"\bwhere\b(?P<body>.*?)(?:\bgroup\s+by\b|\border\s+by\b|\blimit\b|\bunion\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_EQUALITY_PATTERN = re.compile(
    r"^(?:(?P<qualifier>\"?[A-Za-z_][A-Za-z0-9_]*\"?)\.)?"
    r"(?P<column>\"?[A-Za-z_][A-Za-z0-9_]*\"?)\s*=\s*"
    r"(?P<literal>'(?:''|[^'])*'|-?\d+(?:\.\d+)?|true|false)\s*$",
    re.IGNORECASE,
)


def infer_sql_partition_filters(sql_template: str) -> dict[str, dict[str, object]]:
    """Infer safe partition filters from a narrow single-input SQL shape."""
    dataset_refs = sorted(set(INPUT_PATTERN.findall(sql_template)))
    if len(dataset_refs) != 1:
        return {}
    where_clause = _where_clause(sql_template)
    if where_clause is None or _contains_boolean_operator(where_clause, "or"):
        return {}
    alias = _input_alias(sql_template, dataset_refs[0])
    filters = _equality_filters(where_clause, alias)
    return {dataset_refs[0]: filters} if filters else {}


def _input_alias(sql_template: str, dataset_ref: str) -> str | None:
    pattern = re.compile(
        r"\{\{\s*input\('" + re.escape(dataset_ref) + r"'\)\s*\}\}(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?",
        re.IGNORECASE,
    )
    match = pattern.search(sql_template)
    if match is None:
        return None
    alias = match.group("alias")
    if alias is None or alias.lower() in _SQL_KEYWORDS:
        return None
    return alias


def _where_clause(sql_template: str) -> str | None:
    match = _WHERE_PATTERN.search(sql_template)
    if match is None:
        return None
    return match.group("body").strip()


def _equality_filters(where_clause: str, alias: str | None) -> dict[str, object]:
    filters: dict[str, object] = {}
    for condition in _split_boolean_conditions(where_clause, "and"):
        match = _EQUALITY_PATTERN.fullmatch(condition.strip())
        if match is None or not _condition_belongs_to_input(match.group("qualifier"), alias):
            continue
        filters[_identifier_name(match.group("column"))] = _literal_value(match.group("literal"))
    return filters


def _condition_belongs_to_input(qualifier: str | None, alias: str | None) -> bool:
    if qualifier is None:
        return True
    if alias is None:
        return False
    return _identifier_name(qualifier) == alias


def _identifier_name(identifier: str) -> str:
    return identifier[1:-1] if identifier.startswith('"') and identifier.endswith('"') else identifier


def _literal_value(literal: str) -> object:
    if literal.startswith("'"):
        return literal[1:-1].replace("''", "'")
    lowered = literal.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return float(literal) if "." in literal else int(literal)


def _contains_boolean_operator(sql: str, operator: str) -> bool:
    return len(_split_boolean_conditions(sql, operator)) > 1


def _split_boolean_conditions(sql: str, operator: str) -> list[str]:
    parts: list[str] = []
    start = 0
    index = 0
    while index < len(sql):
        if _is_single_quote(sql, index):
            index = _skip_sql_string(sql, index)
            continue
        if _operator_at(sql, index, operator):
            parts.append(sql[start:index].strip())
            index += len(operator)
            start = index
            continue
        index += 1
    parts.append(sql[start:].strip())
    return [part for part in parts if part]


def _is_single_quote(sql: str, index: int) -> bool:
    return sql[index] == "'"


def _skip_sql_string(sql: str, index: int) -> int:
    index += 1
    while index < len(sql):
        if sql[index] == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
            index += 2
        elif sql[index] == "'":
            return index + 1
        else:
            index += 1
    return index


def _operator_at(sql: str, index: int, operator: str) -> bool:
    end = index + len(operator)
    if sql[index:end].lower() != operator:
        return False
    before = sql[index - 1] if index else " "
    after = sql[end] if end < len(sql) else " "
    return not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_")
