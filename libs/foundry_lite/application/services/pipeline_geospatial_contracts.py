"""Typed geospatial field contract and row validation helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


def geospatial_spec(
    schema_contract: Mapping[str, object],
    config: Mapping[str, object],
) -> JsonObject:
    """Resolve GeoJSON or longitude/latitude encoding against actual schema."""

    fields = _schema_fields(schema_contract)
    geometry_field = _optional_text(config.get("geometryField"))
    longitude_field = _optional_text(config.get("longitudeField"))
    latitude_field = _optional_text(config.get("latitudeField"))
    time_field = _optional_text(config.get("timeField"))
    if geometry_field or "geometry" in fields:
        selected = geometry_field or "geometry"
        _require_fields(fields, [selected], "geometryField")
        return _spec("geojson", selected, None, None, time_field, fields)
    longitude = longitude_field or _first_present(fields, ("longitude", "lon", "lng"))
    latitude = latitude_field or _first_present(fields, ("latitude", "lat"))
    _require_fields(fields, [longitude, latitude], "longitude/latitude")
    return _spec("coordinates", None, longitude, latitude, time_field, fields)


def validate_geospatial_rows(
    rows: Sequence[Mapping[str, object]],
    spec: Mapping[str, object],
) -> None:
    """Fail closed when a serving geospatial row has invalid coordinates."""

    if not rows:
        raise ValidationFailed("geospatial artifact requires at least one row")
    invalid = [index for index, row in enumerate(rows) if not _valid_row(row, spec)]
    if invalid:
        raise ValidationFailed(
            "geospatial rows do not satisfy the configured spatial encoding",
            details={"invalidRowIndexes": invalid[:20], "invalidRowCount": len(invalid)},
        )


def _spec(
    encoding: str,
    geometry: str | None,
    longitude: str | None,
    latitude: str | None,
    time_field: str | None,
    fields: set[str],
) -> JsonObject:
    if time_field is not None:
        _require_fields(fields, [time_field], "timeField")
    return {
        "encoding": encoding,
        "geometryField": geometry,
        "longitudeField": longitude,
        "latitudeField": latitude,
        "timeField": time_field,
        "coordinateReferenceSystem": "EPSG:4326",
    }


def _valid_row(row: Mapping[str, object], spec: Mapping[str, object]) -> bool:
    if spec.get("encoding") == "geojson":
        geometry = row.get(str(spec.get("geometryField")))
        return _valid_geometry(geometry)
    longitude = row.get(str(spec.get("longitudeField")))
    latitude = row.get(str(spec.get("latitudeField")))
    return _valid_coordinate(longitude, -180, 180) and _valid_coordinate(latitude, -90, 90)


def _valid_geometry(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    geometry_type = value.get("type")
    coordinates = value.get("coordinates")
    return isinstance(geometry_type, str) and bool(geometry_type.strip()) and isinstance(coordinates, (list, tuple))


def _valid_coordinate(value: object, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return minimum <= float(value) <= maximum


def _schema_fields(schema: Mapping[str, object]) -> set[str]:
    values = schema.get("columns") or schema.get("fields")
    if not isinstance(values, (list, tuple)):
        return set()
    return {str(item["name"]) for item in values if isinstance(item, Mapping) and isinstance(item.get("name"), str)}


def _require_fields(fields: set[str], values: Sequence[str | None], label: str) -> None:
    missing = [value for value in values if value is None or value not in fields]
    if missing:
        raise ValidationFailed(
            "geospatial source schema is missing required fields",
            details={"fieldContract": label, "missingFields": missing, "availableFields": sorted(fields)},
        )


def _first_present(fields: set[str], candidates: Sequence[str]) -> str | None:
    return next((candidate for candidate in candidates if candidate in fields), None)


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
