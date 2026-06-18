"""Each runtime profile's vendor library must ship as an installable extra.

A clean production install only gets `pip install foundry-lite[<profile>]` extras,
not the dev dependency group. If a profile's library lived only in dev, the S3 /
Iceberg / Spark / Temporal / Elasticsearch / Postgres profile would import-fail in
production while passing in development. This guards that mapping.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# selectable runtime profile -> (optional-extra name, required library substring)
_PROFILE_EXTRAS = {
    "s3-storage": ("s3", "boto3"),
    "iceberg": ("iceberg", "pyiceberg"),
    "spark": ("spark", "pyspark"),
    "temporal": ("temporal", "temporalio"),
    "elasticsearch": ("elasticsearch", "elasticsearch"),
    "kafka": ("kafka", "confluent-kafka"),
    "postgres": ("postgres", "psycopg"),
}


def _optional_dependencies() -> dict[str, list[str]]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]


def test_each_runtime_profile_library_is_an_installable_extra() -> None:
    extras = _optional_dependencies()
    for profile, (extra, library) in _PROFILE_EXTRAS.items():
        assert extra in extras, f"profile {profile!r} has no optional extra '{extra}' in pyproject"
        assert any(library in dep for dep in extras[extra]), f"extra '{extra}' must require {library!r}"
