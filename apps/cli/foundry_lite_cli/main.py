from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path

from foundry_lite.application.action_types import ActionApplyResponse
from foundry_lite.application.demo_types import SupplyChainDemoResult
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import (
    DatasetInspectionPayload,
    DatasetRow,
    DatasetVersionRow,
    ObjectSetPayload,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    TabularRow,
    TransformRetryResult,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

JsonObject = dict[str, object]
Handler = Callable[[FoundryLite, RequestContext, argparse.Namespace], object]
DEFAULT_STORAGE_ROOT = ".foundry-lite"
DEFAULT_DEMO_STORAGE_ROOT = ".foundry-lite-demo"


def _foundry(adapter_profile: str | None = None, *, storage_root: str | None = None) -> FoundryLite:
    profile = adapter_profile if adapter_profile is not None else os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local")
    return FoundryLite(
        dependencies=create_local_core_dependencies(
            db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
            storage_root=storage_root or os.getenv("FOUNDRY_LITE_HOME", DEFAULT_STORAGE_ROOT),
            adapter_profile=profile,
        )
    )


def _print(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _params(values: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--param expects key=value, got {value}")
        key, raw = value.split("=", 1)
        params[key] = raw
    return params


def _json_arg(value: str) -> JsonObject:
    parsed: object = json.loads(value)
    if not isinstance(parsed, dict):
        raise SystemExit("JSON argument must be an object")
    return {str(key): item for key, item in parsed.items()}


def _is_supply_chain_demo(args: argparse.Namespace) -> bool:
    return args.group == "demo" and getattr(args, "command", "") == "run-supply-chain"


def _storage_root_for_args(args: argparse.Namespace) -> str | None:
    if os.getenv("FOUNDRY_LITE_HOME") is not None:
        return None
    if _is_supply_chain_demo(args):
        return DEFAULT_DEMO_STORAGE_ROOT
    return DEFAULT_STORAGE_ROOT


def _fresh_supply_chain_demo(args: argparse.Namespace) -> bool:
    if not _is_supply_chain_demo(args):
        return False
    if args.fresh and args.reuse_state:
        raise SystemExit("--fresh and --reuse-state cannot be used together")
    if args.reuse_state:
        return False
    if args.fresh:
        return True
    return os.getenv("FOUNDRY_LITE_HOME") is None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flite")
    parser.add_argument("--adapter-profile", default=os.getenv("FOUNDRY_LITE_ADAPTER_PROFILE", "local"))
    sub = parser.add_subparsers(dest="group", required=True)

    demo = sub.add_parser("demo")
    demo_sub = demo.add_subparsers(dest="command", required=True)
    demo_sub.add_parser("seed")
    demo_run = demo_sub.add_parser("run-supply-chain")
    demo_run.add_argument("--fresh", action="store_true")
    demo_run.add_argument("--reuse-state", action="store_true")

    dataset = sub.add_parser("dataset")
    dataset_sub = dataset.add_subparsers(dest="command", required=True)
    ds_create = dataset_sub.add_parser("create")
    ds_create.add_argument("dataset")
    ds_create.add_argument("--primary-key", action="append", default=[])
    ds_upload = dataset_sub.add_parser("upload")
    ds_upload.add_argument("dataset")
    ds_upload.add_argument("path")
    ds_versions = dataset_sub.add_parser("versions")
    ds_versions.add_argument("dataset")
    ds_preview = dataset_sub.add_parser("preview")
    ds_preview.add_argument("dataset")
    ds_preview.add_argument("--limit", type=int, default=20)
    ds_inspect = dataset_sub.add_parser("inspect")
    ds_inspect.add_argument("dataset")
    ds_inspect.add_argument("--version", default="latest")

    transform = sub.add_parser("transform")
    transform_sub = transform.add_subparsers(dest="command", required=True)
    tr_run = transform_sub.add_parser("run")
    tr_run.add_argument("name")
    tr_retry = transform_sub.add_parser("retry")
    tr_retry.add_argument("run_id")

    ontology = sub.add_parser("ontology")
    ontology_sub = ontology.add_subparsers(dest="command", required=True)
    ont_apply = ontology_sub.add_parser("apply")
    ont_apply.add_argument("path")

    index = sub.add_parser("index")
    index_sub = index.add_subparsers(dest="command", required=True)
    idx_rebuild = index_sub.add_parser("rebuild")
    idx_rebuild.add_argument("object_type")
    idx_replay = index_sub.add_parser("replay")
    idx_replay.add_argument("object_type")
    idx_replay_run = index_sub.add_parser("replay-run")
    idx_replay_run.add_argument("run_id")
    idx_search_rebuild = index_sub.add_parser("rebuild-search")
    idx_search_rebuild.add_argument("object_type")
    idx_search_change = index_sub.add_parser("consume-search-change")
    idx_search_change.add_argument("object_type")
    idx_search_change.add_argument("object_id")

    action = sub.add_parser("action")
    action_sub = action.add_subparsers(dest="command", required=True)
    action_apply = action_sub.add_parser("apply")
    action_apply.add_argument("name")
    action_apply.add_argument("--object", required=True)
    action_apply.add_argument("--expected-version", type=int)
    action_apply.add_argument("--idempotency-key")
    action_apply.add_argument("--param", action="append", default=[])

    materialize = sub.add_parser("materialize")
    materialize_sub = materialize.add_subparsers(dest="command", required=True)
    mat_run = materialize_sub.add_parser("run")
    mat_run.add_argument("name")

    obj = sub.add_parser("object")
    obj_sub = obj.add_subparsers(dest="command", required=True)
    obj_get = obj_sub.add_parser("get")
    obj_get.add_argument("object_type")
    obj_get.add_argument("object_id")
    obj_get.add_argument("--explain", action="store_true")
    obj_links = obj_sub.add_parser("links")
    obj_links.add_argument("object_type")
    obj_links.add_argument("object_id")
    obj_links.add_argument("link_type")

    object_set = sub.add_parser("object-set")
    object_set_sub = object_set.add_subparsers(dest="command", required=True)
    set_list = object_set_sub.add_parser("list")
    set_list.add_argument("--object-type")
    set_get = object_set_sub.add_parser("get")
    set_get.add_argument("set_id")
    set_static = object_set_sub.add_parser("create-static")
    set_static.add_argument("name")
    set_static.add_argument("object_type")
    set_static.add_argument("--id", action="append", required=True)
    set_static.add_argument("--visibility", default="private")
    set_static.add_argument("--ttl-seconds", type=int)
    set_dynamic = object_set_sub.add_parser("create-dynamic")
    set_dynamic.add_argument("name")
    set_dynamic.add_argument("object_type")
    set_dynamic.add_argument("--filter-json", required=True)
    set_dynamic.add_argument("--visibility", default="private")
    set_dynamic.add_argument("--ttl-seconds", type=int)
    object_set_sub.add_parser("cleanup-expired")

    lineage = sub.add_parser("lineage")
    lineage.add_argument("resource_id")

    operations = sub.add_parser("operations")
    operations_sub = operations.add_subparsers(dest="command", required=True)
    operations_runs = operations_sub.add_parser("runs")
    operations_runs.add_argument("--type", dest="run_type")
    operations_runs.add_argument("--status")
    operations_runs.add_argument("--since")
    operations_runs.add_argument("--until")
    operations_runs.add_argument("--limit", type=int, default=50)
    operations_runs.add_argument("--cursor")
    operations_run = operations_sub.add_parser("run")
    operations_run.add_argument("run_type")
    operations_run.add_argument("run_id")

    outbox = sub.add_parser("outbox")
    outbox_sub = outbox.add_subparsers(dest="command", required=True)
    outbox_retry = outbox_sub.add_parser("retry")
    outbox_retry.add_argument("event_id")
    return parser


def _demo_seed(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> dict[str, str]:
    del ctx, args
    foundry.demo.seed_files()
    return {"seeded": str(Path("examples/supply-chain-demo").resolve())}


def _demo_run_supply_chain(
    foundry: FoundryLite,
    ctx: RequestContext,
    args: argparse.Namespace,
) -> SupplyChainDemoResult:
    return foundry.demo.run(ctx=ctx, fresh=_fresh_supply_chain_demo(args))


def _dataset_create(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> DatasetRow:
    return foundry.datasets.create(args.dataset, ctx=ctx, primary_key=args.primary_key)


def _dataset_upload(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> CommitResult:
    return foundry.datasets.upload_csv(args.dataset, args.path, ctx=ctx)


def _dataset_versions(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> list[DatasetVersionRow]:
    return foundry.datasets.list_versions(args.dataset, ctx=ctx)


def _dataset_preview(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> list[TabularRow]:
    return foundry.datasets.preview(args.dataset, ctx=ctx, limit=args.limit)


def _dataset_inspect(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> DatasetInspectionPayload:
    return foundry.datasets.inspect(args.dataset, ctx=ctx, version=args.version)


def _action_apply(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> ActionApplyResponse:
    object_type, object_id = args.object.split("/", 1)
    current = foundry.objects.get(object_type, object_id, ctx=ctx)
    return foundry.actions.apply(
        args.name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=args.expected_version or current["objectVersion"],
        params=_params(args.param),
        idempotency_key=args.idempotency_key or f"{args.name}-{object_type}-{object_id}",
        ctx=ctx,
    )


def _object_set_create_static(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> ObjectSetPayload:
    return foundry.objects.create_set(
        args.name,
        args.object_type,
        set_type="static",
        object_ids=args.id,
        visibility=args.visibility,
        ttl_seconds=args.ttl_seconds,
        ctx=ctx,
    )


def _object_set_create_dynamic(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> ObjectSetPayload:
    return foundry.objects.create_set(
        args.name,
        args.object_type,
        set_type="dynamic",
        filter_ast=_json_arg(args.filter_json),
        visibility=args.visibility,
        ttl_seconds=args.ttl_seconds,
        ctx=ctx,
    )


def _outbox_retry(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> RuntimeRetryResult:
    return foundry.operations.retry_dead_letter_event(args.event_id, ctx=ctx)


def _operations_runs(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> RuntimeRunQueryResult:
    return foundry.operations.query_runs(
        ctx=ctx,
        run_type=args.run_type,
        status=args.status,
        since=args.since,
        until=args.until,
        limit=args.limit,
        cursor=args.cursor,
    )


def _operations_run_detail(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> RuntimeRunDetail:
    return foundry.operations.run_detail(args.run_type, args.run_id, ctx=ctx)


def _transform_retry(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> TransformRetryResult:
    return foundry.transforms.retry_run(args.run_id, ctx=ctx)


def _handlers() -> dict[tuple[str, str], Handler]:
    return {
        ("demo", "seed"): _demo_seed,
        ("demo", "run-supply-chain"): _demo_run_supply_chain,
        ("dataset", "create"): _dataset_create,
        ("dataset", "upload"): _dataset_upload,
        ("dataset", "versions"): _dataset_versions,
        ("dataset", "preview"): _dataset_preview,
        ("dataset", "inspect"): _dataset_inspect,
        ("transform", "run"): lambda foundry, ctx, args: foundry.transforms.run(args.name, ctx=ctx),
        ("transform", "retry"): _transform_retry,
        ("ontology", "apply"): lambda foundry, ctx, args: foundry.ontology.apply(args.path, ctx=ctx),
        ("index", "rebuild"): lambda foundry, ctx, args: foundry.objects.reindex(args.object_type, ctx=ctx),
        ("index", "replay"): lambda foundry, ctx, args: foundry.objects.reindex(args.object_type, ctx=ctx),
        ("index", "replay-run"): lambda foundry, ctx, args: foundry.objects.replay_index_run(args.run_id, ctx=ctx),
        ("index", "rebuild-search"): lambda foundry, ctx, args: foundry.objects.rebuild_search(
            args.object_type, ctx=ctx
        ),
        ("index", "consume-search-change"): lambda foundry, ctx, args: foundry.objects.consume_search_change(
            args.object_type, args.object_id, ctx=ctx
        ),
        ("action", "apply"): _action_apply,
        ("materialize", "run"): lambda foundry, ctx, args: foundry.materialization.run(args.name, ctx=ctx),
        ("object", "get"): lambda foundry, ctx, args: foundry.objects.get(
            args.object_type,
            args.object_id,
            ctx=ctx,
            include_explain=args.explain,
        ),
        ("object", "links"): lambda foundry, ctx, args: foundry.objects.links(
            args.object_type,
            args.object_id,
            args.link_type,
            ctx=ctx,
        ),
        ("object-set", "list"): lambda foundry, ctx, args: foundry.objects.query_sets(
            ctx=ctx,
            object_type_api_name=args.object_type,
        ),
        ("object-set", "get"): lambda foundry, ctx, args: foundry.objects.get_set(args.set_id, ctx=ctx),
        ("object-set", "create-static"): _object_set_create_static,
        ("object-set", "create-dynamic"): _object_set_create_dynamic,
        ("object-set", "cleanup-expired"): lambda foundry, ctx, args: foundry.objects.cleanup_expired_sets(ctx=ctx),
        ("lineage", ""): lambda foundry, ctx, args: foundry.operations.lineage(args.resource_id, ctx=ctx),
        ("operations", "runs"): _operations_runs,
        ("operations", "run"): _operations_run_detail,
        ("outbox", "retry"): _outbox_retry,
    }


def _dispatch(foundry: FoundryLite, ctx: RequestContext, args: argparse.Namespace) -> object:
    key = (args.group, getattr(args, "command", ""))
    handler = _handlers().get(key)
    if handler is None:
        raise SystemExit(f"unsupported command: {' '.join(part for part in key if part)}")
    return handler(foundry, ctx, args)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _print(
        _dispatch(
            _foundry(args.adapter_profile, storage_root=_storage_root_for_args(args)),
            demo_admin_context(),
            args,
        )
    )


if __name__ == "__main__":
    main()
