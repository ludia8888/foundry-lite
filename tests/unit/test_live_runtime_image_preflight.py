from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "quality" / "ensure_live_runtime_images.sh"


def _write_fake_tools(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == image && "$2" == inspect ]]; then\n'
        '  if [[ "$3" == --format ]]; then\n'
        '    record="$(grep -E "^${5}\\|" "${FAKE_IMAGE_DB}" | tail -1)" || exit 1\n'
        "    printf '%s\\n' \"${record#*|}\"\n"
        "  else\n"
        '    grep -E "^${3}\\|" "${FAKE_IMAGE_DB}" >/dev/null 2>&1\n'
        "  fi\n"
        "  exit $?\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(
        "#!/usr/bin/env bash\n"
        'case "$2" in\n'
        '  quality:code-execution-image) image="${FOUNDRY_LITE_CODE_EXECUTION_BUILD_TAG}" ;;\n'
        '  quality:node-code-execution-image) image="${FOUNDRY_LITE_NODE_CODE_EXECUTION_BUILD_TAG}" ;;\n'
        '  quality:trained-model-sidecar-image) image="${FOUNDRY_LITE_TRAINED_MODEL_BUILD_TAG}" ;;\n'
        "  *) exit 3 ;;\n"
        "esac\n"
        'printf \'%s|%s\\n\' "${image}" "${FOUNDRY_LITE_RUNTIME_SOURCE_SHA}" >> "${FAKE_IMAGE_DB}"\n',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    pnpm.chmod(0o755)


def _run_preflight(
    tmp_path: Path,
    profile: str,
    *,
    initial_records: tuple[str, ...] = (),
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    image_db = tmp_path / "images"
    image_db.write_text("".join(f"{record}\n" for record in initial_records), encoding="utf-8")
    _write_fake_tools(bin_dir)
    env = os.environ.copy()
    for variable in (
        "FOUNDRY_LITE_CODE_EXECUTION_IMAGE",
        "FOUNDRY_LITE_CODE_EXECUTION_BUILD_TAG",
        "FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE",
        "FOUNDRY_LITE_NODE_CODE_EXECUTION_BUILD_TAG",
        "FOUNDRY_LITE_TRAINED_MODEL_IMAGE",
        "FOUNDRY_LITE_TRAINED_MODEL_BUILD_TAG",
    ):
        env.pop(variable, None)
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_IMAGE_DB": str(image_db),
        }
    )
    env.update(overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), profile],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_live_preflight_builds_the_runtime_tags_even_with_unrelated_publish_tags(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        "code-execution",
        FOUNDRY_LITE_CODE_EXECUTION_BUILD_TAG="registry.invalid/python:publish-only",
        FOUNDRY_LITE_NODE_CODE_EXECUTION_BUILD_TAG="registry.invalid/node:publish-only",
    )

    assert result.returncode == 0, result.stderr
    images = [record.partition("|")[0] for record in (tmp_path / "images").read_text(encoding="utf-8").splitlines()]
    assert images == [
        "foundry-lite-python-transform:py312-v1",
        "foundry-lite-node-function:node22-v1",
    ]


def test_live_preflight_fails_closed_for_a_missing_configured_runtime_image(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        "trained-model",
        FOUNDRY_LITE_TRAINED_MODEL_IMAGE="registry.invalid/model:missing",
    )

    assert result.returncode == 1
    assert "configured trained-model sidecar image is not available locally" in result.stderr
    assert (tmp_path / "images").read_text(encoding="utf-8") == ""


def test_live_preflight_rebuilds_an_existing_default_image_with_a_stale_source_fingerprint(
    tmp_path: Path,
) -> None:
    result = _run_preflight(
        tmp_path,
        "trained-model",
        initial_records=("foundry-lite-trained-model-transaction-risk:2026.07.1|stale",),
    )

    assert result.returncode == 0, result.stderr
    assert "Rebuild stale live runtime image" in result.stdout
    records = (tmp_path / "images").read_text(encoding="utf-8").splitlines()
    assert len(records) == 2
    assert records[-1].startswith("foundry-lite-trained-model-transaction-risk:2026.07.1|")
    assert not records[-1].endswith("|stale")
