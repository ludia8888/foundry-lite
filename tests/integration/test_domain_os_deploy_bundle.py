"""A generated Domain OS installs, typechecks, tests, and builds outside this monorepo."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from foundry_lite.application.services.aip.fde_domain_os_blueprint import build_domain_os_blueprint
from foundry_lite.application.services.aip.fde_pilot_osdk_bundle import consumer_osdk_plan, react_files


def test_portable_domain_os_bundle_builds_without_the_foundry_lite_workspace(tmp_path: Path) -> None:
    plan = _portable_plan()
    files = react_files(plan)
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    pnpm = shutil.which("pnpm")
    assert pnpm is not None
    commands = (
        (pnpm, "install", "--prefer-offline", "--no-frozen-lockfile"),
        (pnpm, "consumer-osdk:check"),
        (pnpm, "typecheck"),
        (pnpm, "test"),
        (pnpm, "build"),
    )
    for command in commands:
        completed = subprocess.run(command, cwd=tmp_path, capture_output=True, check=False, text=True, timeout=120)
        assert completed.returncode == 0, f"{command}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"

    assert (tmp_path / "dist/index.html").is_file()
    assert "@foundry-lite/sdk" not in (tmp_path / "src/App.tsx").read_text(encoding="utf-8")
    package = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@foundry-lite/property-care-portable-osdk"] == "file:packages/application-osdk"
    app_source = (tmp_path / "src/App.tsx").read_text(encoding="utf-8")
    assert "실행할 수 있는 사람: 시설 담당자" in app_source
    assert "screen.items.map" in app_source


def _portable_plan() -> dict[str, object]:
    arguments = {
        "applicationName": "Property Care Portable",
        "domainDescription": "시설 요청을 접수하고 담당자가 분류한 뒤 완료 증거를 남깁니다.",
        "domainBrief": {
            "actors": ["입주민", "시설 담당자"],
            "records": [
                {
                    "name": "수리 요청",
                    "apiName": "WorkOrder",
                    "fields": [{"name": "심각도", "apiName": "severity", "type": "string", "required": True}],
                }
            ],
            "lifecycleStates": ["REPORTED", "TRIAGED", "COMPLETED"],
            "actions": [
                {
                    "name": "요청 분류",
                    "apiName": "TriageWorkOrder",
                    "fromStates": ["REPORTED"],
                    "toState": "TRIAGED",
                    "requiredInformation": ["우선순위"],
                    "allowedActors": ["시설 담당자"],
                },
                {
                    "name": "수리 완료",
                    "apiName": "CompleteRepair",
                    "fromStates": ["TRIAGED"],
                    "toState": "COMPLETED",
                    "requiredInformation": ["완료 메모"],
                    "allowedActors": ["시설 담당자"],
                },
            ],
            "policies": [
                {
                    "name": "심각도 확인",
                    "statement": "분류 전에 심각도가 기록되어야 합니다.",
                    "enforcement": "blocking",
                    "appliesToActions": ["TriageWorkOrder"],
                    "conditions": [{"propertyApiName": "severity", "operator": "neq", "value": ""}],
                    "evidence": "분류 담당자와 시각",
                }
            ],
            "evidence": ["상태 변경 전후", "담당자", "완료 메모"],
            "integrations": [],
            "successMeasures": ["미완료 누락 0건"],
        },
    }
    return {
        **arguments,
        "domainOsBlueprint": build_domain_os_blueprint(arguments),
        "consumerOsdk": consumer_osdk_plan("Property Care Portable", "property-care-portable"),
    }
