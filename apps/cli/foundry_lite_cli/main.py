from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.domain.context import RequestContext, demo_admin_context

Handler = Callable[[FoundryLiteCore, RequestContext, argparse.Namespace], Any]


def _core() -> FoundryLiteCore:
    return FoundryLiteCore(
        db_url=os.getenv("FOUNDRY_LITE_DB_URL"),
        storage_root=os.getenv("FOUNDRY_LITE_HOME", ".foundry-lite"),
    )


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _params(values: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--param expects key=value, got {value}")
        key, raw = value.split("=", 1)
        params[key] = raw
    return params


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flite")
    sub = parser.add_subparsers(dest="group", required=True)

    demo = sub.add_parser("demo")
    demo_sub = demo.add_subparsers(dest="command", required=True)
    demo_sub.add_parser("seed")
    demo_sub.add_parser("run-supply-chain")

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

    ontology = sub.add_parser("ontology")
    ontology_sub = ontology.add_subparsers(dest="command", required=True)
    ont_apply = ontology_sub.add_parser("apply")
    ont_apply.add_argument("path")

    index = sub.add_parser("index")
    index_sub = index.add_subparsers(dest="command", required=True)
    idx_rebuild = index_sub.add_parser("rebuild")
    idx_rebuild.add_argument("object_type")

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

    lineage = sub.add_parser("lineage")
    lineage.add_argument("resource_id")

    operations = sub.add_parser("operations")
    operations.add_argument("command", choices=["runs"])
    return parser


def _demo_seed(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> dict[str, str]:
    del ctx, args
    core.seed_supply_chain_demo_files()
    return {"seeded": str(Path("examples/supply-chain-demo").resolve())}


def _demo_run_supply_chain(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> dict[str, Any]:
    del args
    return core.run_supply_chain_demo(ctx=ctx)


def _dataset_create(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> dict[str, Any]:
    return core.create_dataset(args.dataset, ctx=ctx, primary_key=args.primary_key)


def _dataset_upload(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> Any:
    return core.upload_csv(args.dataset, args.path, ctx=ctx)


def _dataset_versions(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> list[dict[str, Any]]:
    return core.list_dataset_versions(args.dataset, ctx=ctx)


def _dataset_preview(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> list[dict[str, Any]]:
    return core.preview_dataset(args.dataset, ctx=ctx, limit=args.limit)


def _dataset_inspect(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> dict[str, Any]:
    return core.inspect_dataset(args.dataset, ctx=ctx, version=args.version)


def _action_apply(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> dict[str, Any]:
    object_type, object_id = args.object.split("/", 1)
    current = core.get_object(object_type, object_id, ctx=ctx)
    return core.apply_action(
        args.name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=args.expected_version or current["objectVersion"],
        params=_params(args.param),
        idempotency_key=args.idempotency_key or f"{args.name}-{object_type}-{object_id}",
        ctx=ctx,
    )


def _handlers() -> dict[tuple[str, str], Handler]:
    return {
        ("demo", "seed"): _demo_seed,
        ("demo", "run-supply-chain"): _demo_run_supply_chain,
        ("dataset", "create"): _dataset_create,
        ("dataset", "upload"): _dataset_upload,
        ("dataset", "versions"): _dataset_versions,
        ("dataset", "preview"): _dataset_preview,
        ("dataset", "inspect"): _dataset_inspect,
        ("transform", "run"): lambda core, ctx, args: core.run_transform(args.name, ctx=ctx),
        ("ontology", "apply"): lambda core, ctx, args: core.apply_ontology(args.path, ctx=ctx),
        ("index", "rebuild"): lambda core, ctx, args: core.index_rebuild(args.object_type, ctx=ctx),
        ("action", "apply"): _action_apply,
        ("materialize", "run"): lambda core, ctx, args: core.materialize(args.name, ctx=ctx),
        ("object", "get"): lambda core, ctx, args: core.get_object(
            args.object_type,
            args.object_id,
            ctx=ctx,
            explain=args.explain,
        ),
        ("object", "links"): lambda core, ctx, args: core.get_links(
            args.object_type,
            args.object_id,
            args.link_type,
            ctx=ctx,
        ),
        ("lineage", ""): lambda core, ctx, args: core.lineage_for_resource(args.resource_id, ctx=ctx),
        ("operations", "runs"): lambda core, ctx, args: core.list_runs(ctx=ctx),
    }


def _dispatch(core: FoundryLiteCore, ctx: RequestContext, args: argparse.Namespace) -> Any:
    key = (args.group, getattr(args, "command", ""))
    handler = _handlers().get(key)
    if handler is None:
        raise SystemExit(f"unsupported command: {' '.join(part for part in key if part)}")
    return handler(core, ctx, args)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _print(_dispatch(_core(), demo_admin_context(), args))


if __name__ == "__main__":
    main()
