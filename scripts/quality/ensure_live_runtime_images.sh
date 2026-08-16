#!/usr/bin/env bash
set -euo pipefail

readonly RUNTIME_SOURCE_LABEL="dev.foundry-lite.runtime-source-sha"

source_fingerprint() {
  shasum -a 256 "$@" | LC_ALL=C sort | shasum -a 256 | awk '{print $1}'
}

image_source_fingerprint() {
  docker image inspect \
    --format "{{ index .Config.Labels \"${RUNTIME_SOURCE_LABEL}\" }}" \
    "$1" 2>/dev/null || true
}

ensure_image() {
  local configured_image="$1"
  local default_image="$2"
  local build_script="$3"
  local build_tag_variable="$4"
  local label="$5"
  local expected_fingerprint="$6"
  local resolved_image="${configured_image:-${default_image}}"

  if [[ -n "${configured_image}" ]] && docker image inspect "${resolved_image}" > /dev/null 2>&1; then
    echo "== Live runtime image ready: ${label} (${resolved_image}) =="
    return
  fi

  if [[ -n "${configured_image}" ]]; then
    echo "ERROR: configured ${label} image is not available locally: ${resolved_image}" >&2
    exit 1
  fi

  if docker image inspect "${resolved_image}" > /dev/null 2>&1; then
    local actual_fingerprint
    actual_fingerprint="$(image_source_fingerprint "${resolved_image}")"
    if [[ "${actual_fingerprint}" == "${expected_fingerprint}" ]]; then
      echo "== Live runtime image ready: ${label} (${resolved_image}) =="
      return
    fi
    echo "== Rebuild stale live runtime image: ${label} (${resolved_image}) =="
  else
    echo "== Build missing live runtime image: ${label} (${resolved_image}) =="
  fi
  # The build-only tag may be customized for publishing. A live test without a
  # matching runtime-image override must still build the exact image the adapter
  # will start, rather than silently producing an unrelated tag.
  env \
    "${build_tag_variable}=${resolved_image}" \
    "FOUNDRY_LITE_RUNTIME_SOURCE_SHA=${expected_fingerprint}" \
    pnpm --silent "${build_script}"
  local built_fingerprint
  built_fingerprint="$(image_source_fingerprint "${resolved_image}")"
  if [[ "${built_fingerprint}" != "${expected_fingerprint}" ]]; then
    echo "ERROR: built ${label} image has the wrong source fingerprint" >&2
    exit 1
  fi
}

case "${1:-}" in
  code-execution)
    ensure_image \
      "${FOUNDRY_LITE_CODE_EXECUTION_IMAGE:-}" \
      "foundry-lite-python-transform:py312-v1" \
      "quality:code-execution-image" \
      "FOUNDRY_LITE_CODE_EXECUTION_BUILD_TAG" \
      "isolated Python" \
      "$(source_fingerprint \
        infra/code_execution/Dockerfile \
        libs/foundry_lite/infrastructure/runners/python_transform_runner.py \
        libs/foundry_lite/infrastructure/runners/python_function_runner.py \
        libs/foundry_lite/infrastructure/runners/python_function_osdk.py \
        libs/foundry_lite/transforms_sdk/*.py)"
    ensure_image \
      "${FOUNDRY_LITE_NODE_CODE_EXECUTION_IMAGE:-}" \
      "foundry-lite-node-function:node22-v1" \
      "quality:node-code-execution-image" \
      "FOUNDRY_LITE_NODE_CODE_EXECUTION_BUILD_TAG" \
      "isolated Node" \
      "$(source_fingerprint \
        infra/node_code_execution/Dockerfile \
        libs/foundry_lite/infrastructure/runners/typescript_function_runner.mjs)"
    ;;
  trained-model)
    ensure_image \
      "${FOUNDRY_LITE_TRAINED_MODEL_IMAGE:-}" \
      "foundry-lite-trained-model-transaction-risk:2026.07.1" \
      "quality:trained-model-sidecar-image" \
      "FOUNDRY_LITE_TRAINED_MODEL_BUILD_TAG" \
      "trained-model sidecar" \
      "$(source_fingerprint \
        infra/trained_model_sidecar/Dockerfile \
        libs/foundry_lite/infrastructure/runners/trained_model_runner.py)"
    ;;
  *)
    echo "usage: $0 {code-execution|trained-model}" >&2
    exit 2
    ;;
esac
