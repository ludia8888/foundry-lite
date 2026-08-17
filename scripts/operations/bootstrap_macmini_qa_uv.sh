#!/bin/bash
set -euo pipefail

EXPECTED_USER="sean1234"
EXPECTED_HOME="/Users/sean1234"
QA_ROOT="${EXPECTED_HOME}/foundry-qa"
EXPECTED_REPO="${QA_ROOT}/repo"
UV_VERSION="0.12.5"
UV_ARCHIVE_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-apple-darwin.tar.gz"
UV_ARCHIVE_SHA256="5bb0e5fe008a773c3dbcb97ff79cd89e1241464fe9d2f986d52ad8f1b037bd62"
UV_ARCHIVE_MEMBER="uv-aarch64-apple-darwin/uv"
TARGET="${QA_ROOT}/bin/uv"
METADATA="${QA_ROOT}/state/tool-install-uv.json"

if [[ "$(/usr/bin/id -un)" != "${EXPECTED_USER}" || "${HOME}" != "${EXPECTED_HOME}" ]]; then
  echo "macmini_qa_wrong_unix_principal" >&2
  exit 1
fi
if [[ -L "${QA_ROOT}" || "$(/bin/pwd -P)" != "${EXPECTED_REPO}" ]]; then
  echo "macmini_qa_bootstrap_boundary_invalid" >&2
  exit 1
fi

/bin/mkdir -p "${QA_ROOT}/bin" "${QA_ROOT}/state"
/bin/chmod 700 "${QA_ROOT}" "${QA_ROOT}/bin" "${QA_ROOT}/state" "${EXPECTED_REPO}"
umask 077
temporary="$(/usr/bin/mktemp -d "${QA_ROOT}/state/uv-bootstrap.XXXXXX")"
cleanup() {
  case "${temporary}" in
    "${QA_ROOT}/state/uv-bootstrap."*) /bin/rm -rf -- "${temporary}" ;;
    *) echo "macmini_qa_uv_temporary_path_invalid" >&2 ;;
  esac
}
trap cleanup EXIT
archive="${temporary}/uv.tar.gz"
extracted="${temporary}/uv"

/usr/bin/curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --max-filesize 262144000 --output "${archive}" "${UV_ARCHIVE_URL}"
observed_archive_hash="$(/usr/bin/shasum -a 256 "${archive}" | /usr/bin/awk '{print $1}')"
if [[ "${observed_archive_hash}" != "${UV_ARCHIVE_SHA256}" ]]; then
  echo "macmini_qa_uv_checksum_mismatch" >&2
  exit 1
fi
/usr/bin/tar -xOzf "${archive}" "${UV_ARCHIVE_MEMBER}" > "${extracted}"
/bin/chmod 700 "${extracted}"
if ! /usr/bin/file "${extracted}" | /usr/bin/grep -q 'Mach-O 64-bit executable arm64'; then
  echo "macmini_qa_uv_architecture_invalid" >&2
  exit 1
fi
installed_hash="$(/usr/bin/shasum -a 256 "${extracted}" | /usr/bin/awk '{print $1}')"

if [[ -e "${TARGET}" || -e "${METADATA}" ]]; then
  if [[ ! -f "${TARGET}" || -L "${TARGET}" || ! -f "${METADATA}" || -L "${METADATA}" ]]; then
    echo "macmini_qa_uv_target_conflict" >&2
    exit 1
  fi
  observed_target_hash="$(/usr/bin/shasum -a 256 "${TARGET}" | /usr/bin/awk '{print $1}')"
  if [[ "${observed_target_hash}" != "${installed_hash}" ]] \
    || ! /usr/bin/grep -Fq "${UV_ARCHIVE_SHA256}" "${METADATA}" \
    || ! /usr/bin/grep -Fq "${installed_hash}" "${METADATA}"; then
    echo "macmini_qa_uv_metadata_mismatch" >&2
    exit 1
  fi
  status="already_installed"
else
  /usr/bin/install -m 700 "${extracted}" "${TARGET}"
  /usr/bin/printf '%s\n' \
    "{\"schemaVersion\":1,\"status\":\"installed\",\"tool\":\"uv\",\"path\":\"${TARGET}\",\"sourceUrl\":\"${UV_ARCHIVE_URL}\",\"archiveMember\":\"${UV_ARCHIVE_MEMBER}\",\"downloadSha256\":\"${UV_ARCHIVE_SHA256}\",\"installedFileSha256\":\"${installed_hash}\",\"outsideQaRootWritten\":false}" \
    > "${METADATA}"
  /bin/chmod 600 "${METADATA}"
  status="installed"
fi

"${TARGET}" --version >/dev/null
/usr/bin/printf '%s\n' \
  "{\"schemaVersion\":1,\"status\":\"${status}\",\"tool\":\"uv\",\"downloadSha256\":\"${UV_ARCHIVE_SHA256}\",\"installedFileSha256\":\"${installed_hash}\",\"outsideQaRootWritten\":false}"
