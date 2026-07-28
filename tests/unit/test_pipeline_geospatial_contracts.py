from __future__ import annotations

import pytest
from foundry_lite.application.services.pipeline_geospatial_contracts import (
    geospatial_spec,
    validate_geospatial_rows,
)
from foundry_lite.domain.errors import ValidationFailed


def test_geospatial_spec_resolves_coordinate_fields_from_schema() -> None:
    spec = geospatial_spec(
        {
            "columns": [
                {"name": "longitude", "type": "number"},
                {"name": "latitude", "type": "number"},
                {"name": "observed_at", "type": "timestamp"},
            ]
        },
        {"timeField": "observed_at"},
    )

    assert spec == {
        "encoding": "coordinates",
        "geometryField": None,
        "longitudeField": "longitude",
        "latitudeField": "latitude",
        "timeField": "observed_at",
        "coordinateReferenceSystem": "EPSG:4326",
    }


def test_validate_geospatial_rows_rejects_empty_artifact() -> None:
    with pytest.raises(ValidationFailed, match="at least one row"):
        validate_geospatial_rows([], {"encoding": "geojson", "geometryField": "geometry"})


def test_validate_geospatial_rows_reports_every_invalid_coordinate_row() -> None:
    rows = [
        {"longitude": 127.0, "latitude": 37.5},
        {"longitude": 181.0, "latitude": 37.5},
        {"longitude": 127.0, "latitude": True},
    ]
    spec = {
        "encoding": "coordinates",
        "longitudeField": "longitude",
        "latitudeField": "latitude",
    }

    with pytest.raises(ValidationFailed) as captured:
        validate_geospatial_rows(rows, spec)

    assert captured.value.details == {
        "invalidRowIndexes": [1, 2],
        "invalidRowCount": 2,
    }
