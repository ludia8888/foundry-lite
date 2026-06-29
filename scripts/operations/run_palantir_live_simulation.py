from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404 - fixed local operator commands, no shell.
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.ai_run_repository import AiExecutionRunRecord, AiSessionRecord
from foundry_lite.application.ports.language_model import (
    LanguageModelError,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from foundry_lite.application.ports.model_registry_repository import (
    ModelAliasRecord,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.services.aip.eval_identifiers import hash_json
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository, SqlAlchemyModelRegistryRepository
from foundry_lite.infrastructure.secrets import EnvSecretProvider
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "simulations" / "palantir_live_simulation.json"
USER_AGENT = "Foundry-lite-palantir-live-simulation/1.0"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 1800
DEFAULT_LIVE_LLM_TIMEOUT_SECONDS = 60
DEFAULT_LIVE_LLM_MODEL = "claude-3-5-haiku-latest"
DEFAULT_LIVE_LLM_SECRET_NAME = "foundry_aip_token"
_TENANT = "tenant-live-simulation"
_CTX = RequestContext(tenant_id=_TENANT, actor_user_id="operator-live-simulation", request_id="req-live-simulation")

SimulationStatus = Literal["passed", "failed", "not_run"]


@dataclass(frozen=True)
class SimulationConfig:
    output: Path
    source_limit: int
    command_timeout_seconds: int
    is_gate_run_enabled: bool
    is_cdc_enabled: bool
    is_live_llm_enabled: bool
    media_profile: Literal["none", "smoke", "full"]
    live_llm_model: str
    live_llm_secret_name: str
    live_llm_timeout_seconds: int


@dataclass(frozen=True)
class SourceProbe:
    name: str
    modality: str
    status: SimulationStatus
    observed_count: int
    details: dict[str, object]


@dataclass(frozen=True)
class CommandProbe:
    name: str
    command: list[str]
    status: SimulationStatus
    elapsed_seconds: float
    stdout_tail: str
    stderr_tail: str
    return_code: int | None


@dataclass(frozen=True)
class ProviderProbe:
    name: str
    status: SimulationStatus
    elapsed_seconds: float
    details: dict[str, object]


class _AnthropicGatewayLanguageModel:
    profile_name = "anthropic-live-operator-smoke"

    def __init__(
        self,
        secret_provider: SecretProvider,
        *,
        secret_name: str,
        model: str,
        timeout_seconds: int,
    ) -> None:
        self._secret_provider = secret_provider
        self._secret_name = secret_name
        self._model = model
        self._timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> ModelResponse:
        secret = self._secret_provider.get_secret(self._secret_name)
        started = time.monotonic()
        payload = _anthropic_messages_payload(request, self._model)
        response = _post_anthropic_messages(secret.value, payload, self._timeout_seconds)
        usage = _mapping(response.get("usage"))
        content = _anthropic_text_content(response)
        return ModelResponse(
            provider="anthropic",
            resolved_model_id="",
            resolved_model_revision="",
            content=content,
            finish_reason=str(response.get("stop_reason") or "unknown"),
            input_tokens=_int_value(usage.get("input_tokens")),
            output_tokens=_int_value(usage.get("output_tokens")),
            provider_request_id=str(response.get("id") or ""),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def stream(self, request: ModelRequest) -> Sequence[ModelEvent]:
        del request
        raise LanguageModelError("streaming_unavailable_for_operator_smoke")

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


def main(argv: list[str] | None = None) -> int:
    config = _config(_parser().parse_args(argv))
    evidence = run_simulation(config)
    output = json.dumps(evidence, indent=2, sort_keys=True)
    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if evidence["status"] == "passed" else 1


def run_simulation(config: SimulationConfig) -> dict[str, object]:
    source_probes = [
        _guard_source("usgsEarthquakes", "structured_batch", lambda: _probe_usgs_earthquakes(config.source_limit)),
        _guard_source("wikimediaEventStream", "structured_realtime_sse", lambda: _probe_wikimedia_eventstream()),
        _guard_source(
            "nasaImageVideoLibrary", "image_video_audio_metadata", lambda: _probe_nasa_media(config.source_limit)
        ),
        _guard_source(
            "wikimediaCommonsMedia", "image_audio_video_metadata", lambda: _probe_commons_media(config.source_limit)
        ),
    ]
    provider_probes = _provider_probes(config, source_probes)
    command_probes = _command_probes(config)
    gaps = _simulation_gaps(source_probes, provider_probes, command_probes, config)
    status = "passed" if _is_passed(source_probes, provider_probes, command_probes) else "failed"
    return {
        "status": status,
        "generatedAt": _now_epoch(),
        "operatorIntent": (
            "Palantir-style live simulation across live internet sources, full local infra, "
            "CDC/Debezium and media proofs when enabled, SDK contract, action, AIP, and operations evidence."
        ),
        "config": {
            "sourceLimit": config.source_limit,
            "gateRunEnabled": config.is_gate_run_enabled,
            "cdcEnabled": config.is_cdc_enabled,
            "liveLlmEnabled": config.is_live_llm_enabled,
            "mediaProfile": config.media_profile,
            "liveLlmModel": config.live_llm_model,
        },
        "sourceProbes": [asdict(probe) for probe in source_probes],
        "providerProbes": [asdict(probe) for probe in provider_probes],
        "commandProbes": [asdict(probe) for probe in command_probes],
        "gapsAndNextProofs": gaps,
    }


def _provider_probes(config: SimulationConfig, source_probes: Sequence[SourceProbe]) -> list[ProviderProbe]:
    if not config.is_live_llm_enabled:
        return [
            ProviderProbe(
                name="aip-model-gateway-live-llm",
                status="not_run",
                elapsed_seconds=0.0,
                details={
                    "reason": "disabled by default; pass --include-live-llm and configure the secret env var",
                    "secretName": config.live_llm_secret_name,
                    "supportedEnvVars": [
                        _default_secret_env_var(config.live_llm_secret_name),
                        "ANTHROPIC_API_KEY",
                        "FOUNDRY_LITE_SECRET_ANTHROPIC_API_KEY",
                    ],
                },
            )
        ]
    return [_guard_provider(lambda: _probe_live_llm_gateway(config, source_probes))]


def _command_probes(config: SimulationConfig) -> list[CommandProbe]:
    if not config.is_gate_run_enabled:
        return [
            CommandProbe(
                name="gates",
                command=[],
                status="not_run",
                elapsed_seconds=0.0,
                stdout_tail="",
                stderr_tail="gate execution disabled by --probe-only",
                return_code=None,
            )
        ]
    commands = [
        ("live-data-collection", ["pnpm", "--silent", "quality:live-data-collection"]),
        ("product-all-infra-e2e", ["pnpm", "--silent", "quality:product-all-infra-e2e"]),
        ("sdk-request-contract", ["node", "tests/sdk/request_contract.mjs"]),
    ]
    if config.is_cdc_enabled:
        commands.append(("cdc-live-debezium", ["pnpm", "--silent", "quality:cdc-live-debezium"]))
    if config.media_profile == "smoke":
        commands.extend(
            [
                ("media-live-ocr", ["pnpm", "--silent", "quality:media-live-ocr"]),
                ("media-live-video", ["pnpm", "--silent", "quality:media-live-video"]),
            ]
        )
    if config.media_profile == "full":
        commands.append(("media-active-covered", ["pnpm", "--silent", "quality:media-active-covered"]))
    return [_run_command(name, command, config.command_timeout_seconds) for name, command in commands]


def _run_command(name: str, command: list[str], timeout_seconds: int) -> CommandProbe:
    env = dict(os.environ)
    env.setdefault("DOCKER_HOST", "unix:///Users/isihyeon/.colima/default/docker.sock")
    env.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        return CommandProbe(
            name=name,
            command=command,
            status="failed",
            elapsed_seconds=round(elapsed, 3),
            stdout_tail=_tail(_text_output(exc.stdout)),
            stderr_tail=f"timeout after {timeout_seconds}s\n{_tail(_text_output(exc.stderr))}",
            return_code=None,
        )
    elapsed = time.monotonic() - started
    return CommandProbe(
        name=name,
        command=command,
        status="passed" if completed.returncode == 0 else "failed",
        elapsed_seconds=round(elapsed, 3),
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(completed.stderr),
        return_code=completed.returncode,
    )


def _guard_source(
    name: str,
    modality: str,
    probe: Callable[[], SourceProbe],
) -> SourceProbe:
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - operator evidence records external-source failure shape.
        return SourceProbe(
            name=name,
            modality=modality,
            status="failed",
            observed_count=0,
            details={"errorType": type(exc).__name__, "error": str(exc)},
        )


def _guard_provider(probe: Callable[[], ProviderProbe]) -> ProviderProbe:
    started = time.monotonic()
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - operator evidence records live-provider failure shape.
        elapsed = time.monotonic() - started
        return ProviderProbe(
            name="aip-model-gateway-live-llm",
            status="failed",
            elapsed_seconds=round(elapsed, 3),
            details={"errorType": type(exc).__name__, "error": _safe_error(exc)},
        )


def _probe_live_llm_gateway(config: SimulationConfig, source_probes: Sequence[SourceProbe]) -> ProviderProbe:
    started = time.monotonic()
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    _seed_live_llm_registry(engine, config.live_llm_model, config.live_llm_secret_name)
    ai_run_id = f"ai-run-live-llm-{int(time.time())}"
    _seed_live_ai_run(engine, ai_run_id, config.live_llm_model)
    secret_env_var = _select_live_llm_env_var(config.live_llm_secret_name)
    secret_provider = EnvSecretProvider(env_aliases={config.live_llm_secret_name: secret_env_var})
    gateway = ModelGatewayService(
        engine=engine,
        model_registry_repository=SqlAlchemyModelRegistryRepository(engine),
        language_model_adapter=_AnthropicGatewayLanguageModel(
            secret_provider,
            secret_name=config.live_llm_secret_name,
            model=config.live_llm_model,
            timeout_seconds=config.live_llm_timeout_seconds,
        ),
        ai_run_repository=SqlAlchemyAiRunRepository(engine),
    )
    prompt = _live_llm_prompt(source_probes)
    response = gateway.invoke(
        _CTX,
        ModelRequest(
            model_alias="live-simulation-default",
            messages=(ModelMessage(role="user", content=prompt),),
            max_output_tokens=80,
            request_id="req-live-simulation-llm",
            ai_run_id=ai_run_id,
            request_hash=hash_json(prompt),
            data_classification="public",
            region_requirement="us-east-1",
            timeout_seconds=config.live_llm_timeout_seconds,
        ),
    )
    with engine.begin() as transaction:
        row = transaction.execute(select(db.ai_model_calls)).mappings().one()
    return ProviderProbe(
        name="aip-model-gateway-live-llm",
        status="passed",
        elapsed_seconds=round(time.monotonic() - started, 3),
        details={
            "boundary": "ModelGatewayService",
            "provider": response.provider,
            "model": config.live_llm_model,
            "secretName": config.live_llm_secret_name,
            "secretEnvVar": secret_env_var,
            "aiRunId": ai_run_id,
            "modelCallStatus": row["status"],
            "requestHash": row["request_hash"],
            "responseHash": row["response_hash"],
            "providerRequestId": response.provider_request_id,
            "responseContentHash": _sha256(response.content),
            "responseContentBytes": len(response.content.encode("utf-8")),
            "inputTokens": response.input_tokens,
            "outputTokens": response.output_tokens,
            "latencyMs": response.latency_ms,
        },
    )


def _probe_usgs_earthquakes(limit: int) -> SourceProbe:
    payload = _load_json_url("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson")
    features = _sequence(_mapping(payload).get("features"))[:limit]
    sample = [_usgs_feature_summary(feature) for feature in features if isinstance(feature, Mapping)]
    return SourceProbe(
        name="usgsEarthquakes",
        modality="structured_batch",
        status="passed" if sample else "failed",
        observed_count=len(sample),
        details={
            "sourceUrl": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
            "sample": sample,
        },
    )


def _probe_wikimedia_eventstream() -> SourceProbe:
    request = Request(
        "https://stream.wikimedia.org/v2/stream/recentchange",
        headers={"User-Agent": USER_AGENT, "Accept": "text/event-stream"},
    )
    events: list[dict[str, object]] = []
    deadline = time.monotonic() + 15
    with urlopen(request, timeout=20) as response:  # noqa: S310 - explicit public HTTPS live-source proof.
        while len(events) < 3 and time.monotonic() < deadline:
            line = response.readline().decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue
            payload = json.loads(line.removeprefix("data: "))
            if isinstance(payload, Mapping):
                events.append(_wikimedia_event_summary(payload))
    return SourceProbe(
        name="wikimediaEventStream",
        modality="structured_realtime_sse",
        status="passed" if events else "failed",
        observed_count=len(events),
        details={"sourceUrl": "https://stream.wikimedia.org/v2/stream/recentchange", "sample": events},
    )


def _probe_nasa_media(limit: int) -> SourceProbe:
    query = urlencode({"q": "earth", "media_type": "image,video,audio", "page_size": str(limit)})
    source_url = f"https://images-api.nasa.gov/search?{query}"
    payload = _mapping(_load_json_url(source_url))
    collection = _mapping(payload.get("collection"))
    items = _sequence(collection.get("items"))
    sample = [_nasa_media_summary(item) for item in items[:limit] if isinstance(item, Mapping)]
    media_types = sorted({str(item.get("mediaType")) for item in sample if item.get("mediaType")})
    return SourceProbe(
        name="nasaImageVideoLibrary",
        modality="image_video_audio_metadata",
        status="passed" if sample else "failed",
        observed_count=len(sample),
        details={"sourceUrl": source_url, "mediaTypes": media_types, "sample": sample},
    )


def _probe_commons_media(limit: int) -> SourceProbe:
    query = urlencode(
        {
            "action": "query",
            "generator": "random",
            "grnnamespace": "6",
            "grnlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
            "format": "json",
        }
    )
    source_url = f"https://commons.wikimedia.org/w/api.php?{query}"
    payload = _mapping(_load_json_url(source_url))
    query_payload = _mapping(payload.get("query"))
    pages = _mapping(query_payload.get("pages"))
    sample = [_commons_media_summary(page) for page in pages.values() if isinstance(page, Mapping)]
    media_types = sorted({str(item.get("mime")) for item in sample if item.get("mime")})
    return SourceProbe(
        name="wikimediaCommonsMedia",
        modality="image_audio_video_metadata",
        status="passed" if sample else "failed",
        observed_count=len(sample),
        details={"sourceUrl": source_url, "mimeTypes": media_types, "sample": sample[:limit]},
    )


def _load_json_url(url: str) -> object:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit public HTTPS live-source proof.
        return json.loads(response.read().decode("utf-8"))


def _usgs_feature_summary(feature: Mapping[str, object]) -> dict[str, object]:
    properties = _mapping(feature.get("properties"))
    geometry = _mapping(feature.get("geometry"))
    return {
        "id": feature.get("id"),
        "place": properties.get("place"),
        "magnitude": properties.get("mag"),
        "eventTime": properties.get("time"),
        "coordinates": geometry.get("coordinates"),
        "url": properties.get("url"),
    }


def _wikimedia_event_summary(payload: Mapping[str, object]) -> dict[str, object]:
    meta = _mapping(payload.get("meta"))
    return {
        "id": payload.get("id"),
        "wiki": payload.get("wiki"),
        "type": payload.get("type"),
        "title": payload.get("title"),
        "timestamp": payload.get("timestamp"),
        "stream": meta.get("stream"),
    }


def _nasa_media_summary(item: Mapping[str, object]) -> dict[str, object]:
    data_rows = _sequence(item.get("data"))
    data = _mapping(data_rows[0] if data_rows else {})
    links = _sequence(item.get("links"))
    preview = _mapping(links[0] if links else {})
    return {
        "nasaId": data.get("nasa_id"),
        "title": data.get("title"),
        "mediaType": data.get("media_type"),
        "dateCreated": data.get("date_created"),
        "previewHref": preview.get("href"),
        "assetHref": item.get("href"),
    }


def _commons_media_summary(page: Mapping[str, object]) -> dict[str, object]:
    imageinfo = _sequence(page.get("imageinfo"))
    info = _mapping(imageinfo[0] if imageinfo else {})
    return {
        "pageid": page.get("pageid"),
        "title": page.get("title"),
        "mime": info.get("mime"),
        "size": info.get("size"),
        "url": info.get("url"),
    }


def _anthropic_messages_payload(request: ModelRequest, model: str) -> dict[str, object]:
    return {
        "model": model,
        "max_tokens": request.max_output_tokens,
        "temperature": request.temperature,
        "messages": [{"role": message.role, "content": message.content} for message in request.messages],
    }


def _post_anthropic_messages(api_key: str, payload: Mapping[str, object], timeout_seconds: int) -> Mapping[str, object]:
    request = Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - explicit live LLM drill.
            return _mapping(json.loads(response.read().decode("utf-8")))
    except HTTPError as exc:
        raise LanguageModelError(f"anthropic_http_{exc.code}") from exc
    except URLError as exc:
        raise LanguageModelError("anthropic_network_error") from exc


def _anthropic_text_content(response: Mapping[str, object]) -> str:
    content = _sequence(response.get("content"))
    text_parts: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text":
            text_parts.append(str(part.get("text") or ""))
    return "\n".join(part for part in text_parts if part).strip()


def _live_llm_prompt(source_probes: Sequence[SourceProbe]) -> str:
    source_counts = {probe.name: probe.observed_count for probe in source_probes}
    return (
        "You are validating a Foundry-lite live simulation drill. "
        "Reply in one concise sentence. Confirm whether these public live-source counts look usable "
        f"for a small operator smoke test: {json.dumps(source_counts, sort_keys=True)}."
    )


def _seed_live_llm_registry(engine: Engine, model: str, secret_name: str) -> None:
    repository = SqlAlchemyModelRegistryRepository(engine)
    with engine.begin() as transaction:
        repository.create_provider(
            transaction=transaction,
            record=ModelProviderRecord(
                provider_id="provider-anthropic-live",
                tenant_scope=_TENANT,
                provider_type="anthropic",
                profile_name="anthropic-messages-api",
                region="us-east-1",
                secret_ref=secret_name,
                retention_policy="operator_smoke",
                training_policy="no_train",
                status="active",
                created_at="2026-06-27T00:00:00Z",
            ),
        )
        repository.create_model(
            transaction=transaction,
            record=ModelRecord(
                model_id="model-anthropic-live",
                tenant_scope=_TENANT,
                provider_id="provider-anthropic-live",
                provider_model_id=model,
                revision=model,
                lifecycle="stable",
                capabilities_json={"messages": True, "streaming": False},
                context_limit=200000,
                output_limit=4096,
                pricing_json={},
                allowed_classifications=("public",),
                created_at="2026-06-27T00:00:00Z",
            ),
        )
        repository.create_alias(
            transaction=transaction,
            record=ModelAliasRecord(
                alias="live-simulation-default",
                tenant_scope=_TENANT,
                environment="operator-live",
                model_id="model-anthropic-live",
                version=model,
                status="enabled",
                eval_run_id=None,
                effective_at="2026-06-27T00:00:00Z",
                retired_at=None,
            ),
        )


def _seed_live_ai_run(engine: Engine, ai_run_id: str, model: str) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as transaction:
        repository.create_session(
            transaction=transaction,
            record=AiSessionRecord(
                id=f"{ai_run_id}-session",
                tenant_id=_TENANT,
                agent_version_id="agent.live-simulation.v1",
                actor_user_id="operator-live-simulation",
                status="active",
                created_at="2026-06-27T00:00:00Z",
                last_activity_at="2026-06-27T00:00:00Z",
            ),
        )
        repository.create_execution_run(
            transaction=transaction,
            record=AiExecutionRunRecord(
                id=ai_run_id,
                tenant_id=_TENANT,
                session_id=f"{ai_run_id}-session",
                agent_version_id="agent.live-simulation.v1",
                actor_user_id="operator-live-simulation",
                request_id="req-live-simulation-llm",
                trace_id=f"{ai_run_id}-trace",
                status="running",
                ontology_version_id="live-simulation-ontology",
                model_alias_version="live-simulation-default",
                resolved_model_id="model-anthropic-live",
                resolved_model_revision=model,
                prompt_version_id="prompt.live-simulation@v1",
                compiled_prompt_hash="sha256:pending",
                tool_manifest_hash="sha256:no-tools",
                context_manifest_hash="sha256:live-source-counts",
                state_snapshot_hash="sha256:empty",
                policy_snapshot_hash="sha256:public-only",
                budget_json={"maxModelCalls": 1, "maxOutputTokens": 80},
                usage_json=None,
                error_json=None,
                started_at="2026-06-27T00:00:00Z",
                completed_at=None,
            ),
        )


def _select_live_llm_env_var(secret_name: str) -> str:
    candidates = [
        _default_secret_env_var(secret_name),
        "ANTHROPIC_API_KEY",
        "FOUNDRY_LITE_SECRET_ANTHROPIC_API_KEY",
    ]
    return next((candidate for candidate in candidates if os.environ.get(candidate, "") != ""), candidates[0])


def _default_secret_env_var(secret_name: str) -> str:
    normalized = "".join(char if char.isalnum() else "_" for char in secret_name).upper()
    return f"FOUNDRY_LITE_SECRET_{normalized}"


def _simulation_gaps(
    source_probes: Sequence[SourceProbe],
    provider_probes: Sequence[ProviderProbe],
    command_probes: Sequence[CommandProbe],
    config: SimulationConfig,
) -> list[dict[str, object]]:
    gaps: list[dict[str, object]] = []
    if config.media_profile == "none":
        gaps.append(
            {
                "gap": "internet_media_bytes_to_media_processing_not_executed",
                "whyItMatters": (
                    "The source probe proves live image/video/audio metadata is reachable, but the default smoke does "
                    "not download those public media bytes into the Media plane. Existing media gates prove OCR/ASR/"
                    "video/embedding processing on live MinIO and Elasticsearch."
                ),
                "nextCommand": "pnpm --silent simulation:palantir-live-media",
            }
        )
    if not config.is_cdc_enabled:
        gaps.append(
            {
                "gap": "debezium_cdc_not_executed_in_default_smoke",
                "whyItMatters": (
                    "The default smoke avoids a Debezium Connect container. CDC is available as an explicit drill."
                ),
                "nextCommand": "pnpm --silent simulation:palantir-live-cdc",
            }
        )
    gaps.append(
        {
            "gap": "true_big_data_scale_is_laptop_bounded",
            "whyItMatters": (
                "This drill uses real live seeds and real local infrastructure, but it is not a managed cluster or "
                "petabyte-scale public data firehose. Large-volume proof should use sustained/stress profiles with "
                "row/media amplification and explicit resource budgets."
            ),
            "nextProof": (
                "Add --duration/--event-count stress mode with Kafka producer amplification "
                "and Spark/ES latency metrics."
            ),
        }
    )
    if not config.is_live_llm_enabled:
        gaps.append(
            {
                "gap": "real_external_llm_provider_not_used_in_default_smoke",
                "whyItMatters": (
                    "The default smoke avoids paid/rate-limited external LLM calls. "
                    "AIP still proves context retrieval, prompt artifacts, model-call ledger, citations, "
                    "and operations evidence through local/fake paths."
                ),
                "nextCommand": (
                    "FOUNDRY_LITE_SECRET_FOUNDRY_AIP_TOKEN=<redacted> pnpm --silent simulation:palantir-live-llm"
                ),
            }
        )
    failed_sources = [probe.name for probe in source_probes if probe.status != "passed"]
    failed_providers = [probe.name for probe in provider_probes if probe.status == "failed"]
    failed_commands = [probe.name for probe in command_probes if probe.status == "failed"]
    if failed_sources or failed_providers or failed_commands:
        gaps.append(
            {
                "gap": "simulation_failures",
                "failedSources": failed_sources,
                "failedProviders": failed_providers,
                "failedCommands": failed_commands,
            }
        )
    return gaps


def _is_passed(
    source_probes: Sequence[SourceProbe],
    provider_probes: Sequence[ProviderProbe],
    command_probes: Sequence[CommandProbe],
) -> bool:
    return (
        all(probe.status == "passed" for probe in source_probes)
        and all(probe.status in {"passed", "not_run"} for probe in provider_probes)
        and all(probe.status in {"passed", "not_run"} for probe in command_probes)
    )


def _text_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 500 else f"{text[:500]}..."


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _tail(value: str, *, max_chars: int = 4000) -> str:
    return value[-max_chars:]


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> list[object]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _now_epoch() -> float:
    return round(time.time(), 3)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Palantir-style live simulation drill.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-limit", type=int, default=5)
    parser.add_argument("--command-timeout-seconds", type=int, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--include-cdc", action="store_true")
    parser.add_argument("--include-live-llm", action="store_true")
    parser.add_argument("--media-profile", choices=("none", "smoke", "full"), default="none")
    parser.add_argument("--live-llm-model", default=DEFAULT_LIVE_LLM_MODEL)
    parser.add_argument("--live-llm-secret-name", default=DEFAULT_LIVE_LLM_SECRET_NAME)
    parser.add_argument("--live-llm-timeout-seconds", type=int, default=DEFAULT_LIVE_LLM_TIMEOUT_SECONDS)
    return parser


def _config(args: argparse.Namespace) -> SimulationConfig:
    return SimulationConfig(
        output=cast(Path, args.output),
        source_limit=max(1, int(args.source_limit)),
        command_timeout_seconds=max(60, int(args.command_timeout_seconds)),
        is_gate_run_enabled=not bool(args.probe_only),
        is_cdc_enabled=bool(args.include_cdc),
        is_live_llm_enabled=bool(args.include_live_llm),
        media_profile=cast(Literal["none", "smoke", "full"], args.media_profile),
        live_llm_model=str(args.live_llm_model),
        live_llm_secret_name=str(args.live_llm_secret_name),
        live_llm_timeout_seconds=max(10, int(args.live_llm_timeout_seconds)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
