"""Fail closed when the production-shaped Kubernetes package loses its safety contract.

Enforces guideline §4.3 Scale Foundation and Infra Swap rules.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "quality" / "kubernetes_packaging.json"
CHART_ROOT = Path("deploy/helm/foundry-lite")
TOOL_MANIFEST = Path("deploy/macmini-tools-arm64.json")
REQUIRED_PATHS = (
    CHART_ROOT / "Chart.yaml",
    CHART_ROOT / "values.yaml",
    CHART_ROOT / "values.macmini-qa.yaml",
    CHART_ROOT / "values.embedded-oauth-smoke.yaml",
    CHART_ROOT / "values.ci.yaml",
    CHART_ROOT / "values.schema.json",
    CHART_ROOT / "crds/foundrydeployments.yaml",
    CHART_ROOT / "templates/_helpers.tpl",
    CHART_ROOT / "templates/api.yaml",
    CHART_ROOT / "templates/configmap.yaml",
    CHART_ROOT / "templates/web.yaml",
    CHART_ROOT / "templates/web-config.yaml",
    CHART_ROOT / "templates/workers.yaml",
    CHART_ROOT / "templates/release-controller.yaml",
    CHART_ROOT / "templates/execution-broker.yaml",
    CHART_ROOT / "templates/jobs.yaml",
    CHART_ROOT / "templates/networkpolicies.yaml",
    CHART_ROOT / "templates/qa-datastores.yaml",
    CHART_ROOT / "templates/qa-dependencies-config.yaml",
    CHART_ROOT / "templates/qa-observability-identity.yaml",
    CHART_ROOT / "templates/qa-runtime-services.yaml",
    CHART_ROOT / "templates/runtime-pvc.yaml",
    CHART_ROOT / "templates/serviceaccounts-rbac.yaml",
    Path("deploy/kubernetes/Dockerfile.api"),
    Path("deploy/kubernetes/Dockerfile.web"),
    Path("deploy/kubernetes/Dockerfile.controller"),
    Path("infra/code_execution/Dockerfile.arm64"),
    Path("infra/node_code_execution/Dockerfile.arm64"),
    Path("infra/trained_model_sidecar/Dockerfile.arm64"),
    Path(".github/workflows/kubernetes-images.yml"),
    Path("scripts/operations/bootstrap_keycloak_qa_user.py"),
    Path("scripts/operations/bootstrap_macmini_qa_secrets.py"),
    Path("scripts/operations/deploy_macmini_qa.py"),
    Path("scripts/operations/install_macmini_qa_tool.py"),
    Path("scripts/operations/bootstrap_macmini_qa_uv.sh"),
    TOOL_MANIFEST,
    Path("apps/worker/foundry_lite_worker/outbox_publisher.py"),
    Path("apps/worker/foundry_lite_worker/source_scheduler.py"),
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_COMPONENT_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._-")
_IMAGE_EDGE_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


@dataclass(frozen=True, slots=True)
class KubernetesPackagingFinding:
    code: str
    path: str
    detail: str


def collect_findings(root: Path = ROOT) -> list[KubernetesPackagingFinding]:
    findings = _missing_file_findings(root)
    if findings:
        return findings
    return [
        *_values_findings(root),
        *_dockerfile_findings(root),
        *_workflow_findings(root),
        *_crd_findings(root),
        *_template_findings(root),
        *_rbac_findings(root),
        *_execution_isolation_findings(root),
        *_tool_manifest_findings(root),
        *_operation_findings(root),
    ]


def _missing_file_findings(root: Path) -> list[KubernetesPackagingFinding]:
    return [
        _finding("missing_file", path, "required Kubernetes package file is missing")
        for path in REQUIRED_PATHS
        if not (root / path).is_file()
    ]


def _values_findings(root: Path) -> list[KubernetesPackagingFinding]:
    path = CHART_ROOT / "values.yaml"
    payload = _yaml_mapping(root / path)
    dependencies = _mapping(payload.get("qaDependencies"))
    findings: list[KubernetesPackagingFinding] = []
    for name in (
        "postgresql",
        "minio",
        "redpanda",
        "temporal",
        "elasticsearch",
        "clamav",
        "prometheus",
        "tempo",
        "grafana",
        "keycloak",
    ):
        image = _mapping(dependencies.get(name)).get("image")
        if not isinstance(image, str) or not _is_digest_image(image):
            findings.append(_finding("mutable_dependency_image", path, name))
    images = _mapping(_yaml_mapping(root / (CHART_ROOT / "values.ci.yaml")).get("images"))
    for name in ("api", "web", "controller", "codeExecution", "nodeCodeExecution", "trainedModel"):
        digest = _mapping(images.get(name)).get("digest")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            findings.append(_finding("invalid_ci_image_digest", CHART_ROOT / "values.ci.yaml", name))
    if dependencies.get("enabled") is not False:
        findings.append(_finding("qa_dependencies_default_on", path, "generic chart must use external infrastructure"))
    return findings


def _is_digest_image(value: str) -> bool:
    if len(value) > 2048 or value.count("@") != 1:
        return False
    repository, digest = value.split("@", 1)
    components = repository.split("/")
    if not _DIGEST.fullmatch(digest) or len(components) < 2:
        return False
    return all(_is_image_component(component) for component in components)


def _is_image_component(value: str) -> bool:
    if not value or len(value) > 128:
        return False
    if value[0] not in _IMAGE_EDGE_CHARACTERS or value[-1] not in _IMAGE_EDGE_CHARACTERS:
        return False
    return all(character in _IMAGE_COMPONENT_CHARACTERS for character in value)


def _dockerfile_findings(root: Path) -> list[KubernetesPackagingFinding]:
    findings: list[KubernetesPackagingFinding] = []
    for relative in REQUIRED_PATHS:
        if "Dockerfile" not in relative.name:
            continue
        for line in (root / relative).read_text(encoding="utf-8").splitlines():
            if not line.startswith("FROM "):
                continue
            reference = line.split()[1]
            if reference != "${API_BASE_IMAGE}" and "@sha256:" not in reference:
                findings.append(_finding("mutable_base_image", relative, reference))
    return findings


def _workflow_findings(root: Path) -> list[KubernetesPackagingFinding]:
    path = Path(".github/workflows/kubernetes-images.yml")
    text = (root / path).read_text(encoding="utf-8")
    required = (
        "platforms: linux/arm64",
        "sbom: true",
        "provenance: mode=max",
        "cosign sign --yes",
        "cosign verify",
        "crane config --platform linux/arm64",
        '.architecture == "arm64"',
        "sha-${{ env.REVISION }}",
        "org.opencontainers.image.revision",
        "id-token: write",
    )
    return [_finding("image_supply_chain_contract_missing", path, term) for term in required if term not in text]


def _crd_findings(root: Path) -> list[KubernetesPackagingFinding]:
    path = CHART_ROOT / "crds/foundrydeployments.yaml"
    payload = _yaml_mapping(root / path)
    spec = _mapping(payload.get("spec"))
    versions = spec.get("versions")
    text = (root / path).read_text(encoding="utf-8")
    required = (
        "commitId",
        "imageRepository",
        "workloadRef",
        "idempotencyKeyHash",
        "rollbackTargetDeployId",
        "subresources:",
        "status: {}",
        "self == oldSelf",
        "imageDigest",
        "isSignatureVerified",
        "isLinuxArm64",
    )
    findings = [_finding("crd_contract_missing", path, term) for term in required if term not in text]
    if spec.get("scope") != "Namespaced" or not isinstance(versions, list):
        findings.append(_finding("crd_scope_invalid", path, "FoundryDeployment must be namespaced and versioned"))
    return findings


def _template_findings(root: Path) -> list[KubernetesPackagingFinding]:
    template_paths = tuple(path for path in REQUIRED_PATHS if path.parent.name == "templates")
    text = "\n".join((root / path).read_text(encoding="utf-8") for path in template_paths)
    required = (
        "readOnlyRootFilesystem: true",
        "allowPrivilegeEscalation: false",
        'drop: ["ALL"]',
        "seccompProfile:",
        "runAsNonRoot: true",
        "kind: NetworkPolicy",
        "kind: PodDisruptionBudget",
        "readinessProbe:",
        "livenessProbe:",
        "resources:",
        "persistentVolumeClaim:",
        "helm.sh/hook: pre-install,pre-upgrade",
        "foundry-lite.io/migration-contract: idempotent-twice",
    )
    findings = [_finding("workload_safety_contract_missing", CHART_ROOT, term) for term in required if term not in text]
    for forbidden in ("docker.sock", "hostPath:", "privileged: true", "hostNetwork: true"):
        if forbidden in text:
            findings.append(_finding("forbidden_host_escape", CHART_ROOT, forbidden))
    identity = (root / (CHART_ROOT / "templates/qa-dependencies-config.yaml")).read_text(encoding="utf-8")
    identity_runtime = (root / (CHART_ROOT / "templates/qa-observability-identity.yaml")).read_text(encoding="utf-8")
    identity_terms = (
        '"executor": "secure-redirect-uris-enforcer"',
        '"oauth-2-1-compliant": "true"',
        '"executor": "pkce-enforcer"',
        '"executor": "consent-required"',
        '"providerId": "max-clients"',
        '"is.parameterized.scope": "true"',
        '"protocolMapper": "oidc-parameterized-scope-mapper"',
        '"claim.name": "aud"',
        "bootstrap_keycloak_qa_user.py",
        "KEYCLOAK_QA_AUTHOR_USER_PASSWORD",
        "KEYCLOAK_QA_REVIEWER_USER_PASSWORD",
    )
    identity_text = identity + identity_runtime
    for term in identity_terms:
        if term not in identity_text:
            findings.append(_finding("keycloak_policy_contract_missing", CHART_ROOT, term))
    return findings


def _rbac_findings(root: Path) -> list[KubernetesPackagingFinding]:
    path = CHART_ROOT / "templates/serviceaccounts-rbac.yaml"
    text = (root / path).read_text(encoding="utf-8")
    api_marker = 'kind: Role\nmetadata:\n  name: {{ include "foundry-lite.fullname" . }}-api-release'
    api_role = _section(text, api_marker, "---")
    controller_role = _section(
        text,
        'kind: Role\nmetadata:\n  name: {{ include "foundry-lite.fullname" . }}-release-controller',
        "---",
    )
    findings: list[KubernetesPackagingFinding] = []
    if 'resources: ["secrets"]' in api_role or 'resources: ["secrets"]' in controller_role:
        findings.append(_finding("release_secret_rbac_forbidden", path, "API/controller cannot read Secrets"))
    if 'resources: ["foundrydeployments"]' not in api_role or 'verbs: ["create", "get", "list"]' not in api_role:
        findings.append(_finding("api_release_rbac_missing", path, "FoundryDeployment create/get/list"))
    has_status = 'resources: ["foundrydeployments/status"]' in controller_role
    has_deployment = 'resources: ["deployments"]' in controller_role
    if not has_status or not has_deployment:
        findings.append(_finding("controller_release_rbac_missing", path, "status/deployment patch"))
    if 'resources: ["jobs"]' in api_role:
        findings.append(_finding("api_execution_rbac_forbidden", path, "API cannot create Kubernetes Jobs"))
    return findings


def _execution_isolation_findings(root: Path) -> list[KubernetesPackagingFinding]:
    broker_path = CHART_ROOT / "templates/execution-broker.yaml"
    network_path = CHART_ROOT / "templates/networkpolicies.yaml"
    broker = (root / broker_path).read_text(encoding="utf-8")
    network = (root / network_path).read_text(encoding="utf-8")
    findings: list[KubernetesPackagingFinding] = []
    required_broker = (
        'resources: ["jobs"]',
        'verbs: ["create", "get", "delete"]',
        'resources: ["pods"]',
        'verbs: ["get", "list"]',
        "foundry_lite_worker.kubernetes_execution_broker",
        "automountServiceAccountToken: true",
    )
    for term in required_broker:
        if term not in broker:
            findings.append(_finding("execution_broker_contract_missing", broker_path, term))
    if 'resources: ["secrets"]' in broker or "docker.sock" in broker or "hostPath:" in broker:
        findings.append(_finding("execution_broker_privilege_forbidden", broker_path, "secret or host access"))
    required_network = (
        'name: {{ include "foundry-lite.fullname" . }}-default-deny',
        "podSelector: {}",
        "foundry-lite.io/execution-sandbox",
        "operator: DoesNotExist",
        "values: [execution-sandbox, execution-broker]",
    )
    for term in required_network:
        if term not in network:
            findings.append(_finding("execution_network_deny_contract_missing", network_path, term))
    return findings


def _operation_findings(root: Path) -> list[KubernetesPackagingFinding]:
    secret_path = Path("scripts/operations/bootstrap_macmini_qa_secrets.py")
    deploy_path = Path("scripts/operations/deploy_macmini_qa.py")
    worker_paths = (
        Path("apps/worker/foundry_lite_worker/outbox_publisher.py"),
        Path("apps/worker/foundry_lite_worker/source_scheduler.py"),
    )
    secret_text = (root / secret_path).read_text(encoding="utf-8")
    deploy_text = (root / deploy_path).read_text(encoding="utf-8")
    findings = _missing_term_findings(
        secret_text,
        secret_path,
        "protected_secret_contract_missing",
        (
            "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY",
            "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY",
            '"GRAFANA_ADMIN_USER"',
            '"GRAFANA_ADMIN_PASSWORD"',
            '"foundry-lite-ghcr"',
            '"kubernetes.io/dockerconfigjson"',
            "registry_token_file",
            '"immutable": True',
        ),
    )
    installer_path = Path("scripts/operations/install_macmini_qa_tool.py")
    installer_text = (root / installer_path).read_text(encoding="utf-8")
    findings.extend(
        _missing_term_findings(
            installer_text,
            installer_path,
            "macmini_bootstrap_tool_missing",
            ('"age-keygen"', '"uv"', "install_manifest", "macmini-tools-arm64.json"),
        )
    )
    uv_bootstrap_path = Path("scripts/operations/bootstrap_macmini_qa_uv.sh")
    uv_bootstrap_text = (root / uv_bootstrap_path).read_text(encoding="utf-8")
    findings.extend(_uv_bootstrap_findings(uv_bootstrap_text, uv_bootstrap_path))
    findings.extend(_deploy_contract_findings(deploy_text, deploy_path))
    findings.extend(_worker_composition_findings(root, worker_paths))
    return findings


def _tool_manifest_findings(root: Path) -> list[KubernetesPackagingFinding]:
    expected_names = {"uv", "kubectl", "helm", "age", "age-keygen", "cosign", "crane", "kubeconform"}
    values, loading_findings = _load_tool_manifest(root)
    if loading_findings:
        return loading_findings
    findings: list[KubernetesPackagingFinding] = []
    names: list[str] = []
    for value in values:
        name, value_findings = _tool_manifest_entry_findings(value)
        names.append(name)
        findings.extend(value_findings)
    if len(names) != len(expected_names) or set(names) != expected_names:
        findings.append(_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "exact tool allowlist"))
    return findings


def _load_tool_manifest(root: Path) -> tuple[list[object], list[KubernetesPackagingFinding]]:
    try:
        payload = json.loads((root / TOOL_MANIFEST).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [], [_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "JSON")]
    if not isinstance(payload, dict):
        return [], [_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "mapping")]
    values = payload.get("tools")
    header_checks = (
        payload.get("schemaVersion") == "foundry-lite-macmini-tools/v1",
        payload.get("platform") == "darwin-arm64",
        isinstance(values, list),
    )
    if not all(header_checks) or not isinstance(values, list):
        return [], [_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "header")]
    return values, []


def _tool_manifest_entry_findings(value: object) -> tuple[str, list[KubernetesPackagingFinding]]:
    if not isinstance(value, dict):
        return "", [_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "tool mapping")]
    name = value.get("name")
    version = value.get("version")
    url = urllib.parse.urlsplit(str(value.get("url", "")))
    digest = str(value.get("sha256", ""))
    member = value.get("archiveMember")
    findings: list[KubernetesPackagingFinding] = []
    if set(value) != {"name", "version", "url", "sha256", "archiveMember"}:
        findings.append(_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "tool fields"))
    field_checks = (
        isinstance(name, str),
        isinstance(version, str),
        bool(version),
        url.scheme == "https",
        bool(url.hostname),
        bool(re.fullmatch(r"[0-9a-f]{64}", digest)),
    )
    if not all(field_checks):
        findings.append(_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, str(name or "")))
    if not _is_safe_archive_member(member):
        findings.append(_finding("macmini_tool_manifest_invalid", TOOL_MANIFEST, "archive member"))
    return name if isinstance(name, str) else "", findings


def _is_safe_archive_member(member: object) -> bool:
    if member is None:
        return True
    if not isinstance(member, str):
        return False
    return not member.startswith("/") and ".." not in Path(member).parts


def _missing_term_findings(
    text: str,
    path: Path,
    code: str,
    terms: tuple[str, ...],
) -> list[KubernetesPackagingFinding]:
    return [_finding(code, path, term) for term in terms if term not in text]


def _uv_bootstrap_findings(text: str, path: Path) -> list[KubernetesPackagingFinding]:
    terms = (
        'EXPECTED_USER="sean1234"',
        'EXPECTED_HOME="/Users/sean1234"',
        "UV_ARCHIVE_SHA256=",
        "tool-install-uv.json",
        "Mach-O 64-bit executable arm64",
    )
    return _missing_term_findings(text, path, "macmini_uv_bootstrap_contract_missing", terms)


def _deploy_contract_findings(text: str, path: Path) -> list[KubernetesPackagingFinding]:
    terms = (
        "--atomic",
        "--wait-for-jobs",
        '"migrations": {"enabled": False}',
        "_IMAGE_REPOSITORIES",
        '"--registry-token-file"',
    )
    return _missing_term_findings(text, path, "macmini_deploy_contract_missing", terms)


def _worker_composition_findings(
    root: Path,
    paths: tuple[Path, ...],
) -> list[KubernetesPackagingFinding]:
    findings: list[KubernetesPackagingFinding] = []
    for path in paths:
        text = (root / path).read_text(encoding="utf-8")
        is_runtime = "create_runtime_core_dependencies" in text
        is_local = "create_local_core_dependencies" in text
        if not is_runtime or is_local:
            findings.append(_finding("protected_worker_composition_invalid", path, "runtime composition root"))
    return findings


def _section(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    tail = text.split(start, 1)[1]
    return tail.split(end, 1)[0]


def _yaml_mapping(path: Path) -> dict[object, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid YAML mapping: {path}")
    return payload


def _mapping(value: object) -> dict[object, object]:
    return value if isinstance(value, dict) else {}


def _finding(code: str, path: Path, detail: str) -> KubernetesPackagingFinding:
    return KubernetesPackagingFinding(code=code, path=path.as_posix(), detail=detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    findings = collect_findings(args.root.resolve())
    output = args.output if args.output.is_absolute() else args.root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"gate_pass": not findings, "findings": [asdict(item) for item in findings]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"kubernetes packaging gate: {'PASS' if not findings else 'FAIL'} ({len(findings)} findings)")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
