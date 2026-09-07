"""Prevent reintroducing dependency versions rejected by the September release audit."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("name", "minimum", "vulnerable"),
    [("gitpython", "3.1.59", "3.1.58"), ("pypdf", "6.16.1", "6.16.0")],
)
def test_declared_and_locked_dependencies_exclude_known_vulnerable_versions(
    name: str, minimum: str, vulnerable: str
) -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    locked = tomllib.loads((ROOT / "uv.lock").read_text())
    declarations = [*project["project"]["dependencies"], *project["dependency-groups"]["dev"]]
    requirement = next(Requirement(item) for item in declarations if Requirement(item).name.lower() == name)
    versions = [Version(item["version"]) for item in locked["package"] if item["name"] == name]

    assert Version(vulnerable) not in requirement.specifier
    assert Version(minimum) in requirement.specifier
    assert versions and all(version >= Version(minimum) for version in versions)
