"""Compile only a business blueprint that is ready for Pilot generation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_domain_os_blueprint import (
    application_resources,
    build_business_system_definition,
    build_domain_os_blueprint,
    ontology_resources,
    require_ready_blueprint,
    seed_plan,
)
from foundry_lite.application.services.aip.fde_pilot_osdk_bundle import consumer_osdk_plan
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, required_text
from foundry_lite.domain.errors import ValidationFailed

JsonObject = Mapping[str, object]


def build_preview_pilot_plan(arguments: JsonObject) -> dict[str, object]:
    """Return review questions without compiling missing Workshop resources."""

    app_name = required_text(arguments, "applicationName")
    description = required_text(arguments, "domainDescription")
    brief = arguments.get("domainBrief")
    if not isinstance(brief, Mapping):
        raise FdePlatformToolError("schema_invalid", "domainBrief must be an object")
    slug = _pilot_slug(app_name)
    blueprint = build_domain_os_blueprint(arguments)
    preview = {
        "operationType": "pilot_generation_plan",
        "applicationName": app_name,
        "domainDescription": description,
        "domainBrief": dict(brief),
        "domainOsBlueprint": blueprint,
        "slug": slug,
        "projectDisplayName": f"{app_name} Pilot",
    }
    readiness = blueprint.get("readiness")
    if not isinstance(readiness, Mapping) or readiness.get("isReady") is not True:
        return {**preview, "requiredApprovals": []}
    return {**preview, **compilable_pilot_plan(app_name, slug, pilot_identifier(slug), blueprint)}


def normalized_pilot_plan(plan: JsonObject) -> dict[str, object]:
    """Recompile from the original business brief before any mutation."""

    normalized = {str(name): value for name, value in plan.items()}
    app_name = required_text(normalized, "applicationName")
    normalized["domainDescription"] = required_text(normalized, "domainDescription")
    slug = _pilot_slug(app_name)
    blueprint = build_domain_os_blueprint(normalized)
    require_ready_blueprint(blueprint)
    normalized["slug"] = slug
    normalized["projectDisplayName"] = f"{app_name} Pilot"
    normalized["domainOsBlueprint"] = blueprint
    normalized.update(compilable_pilot_plan(app_name, slug, pilot_identifier(slug), blueprint))
    return normalized


def compilable_pilot_plan(app_name: str, slug: str, identifier: str, blueprint: JsonObject) -> dict[str, object]:
    """Keep Workshop and deployable resources out of incomplete previews."""

    records = _items(blueprint.get("records"))
    workflow = blueprint.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValidationFailed("Domain OS blueprint workflow is missing")
    actions = _items(workflow.get("actions"))
    functions = _items(blueprint.get("functions") or [])
    consumer_osdk = consumer_osdk_plan(app_name, slug)
    return {
        "businessSystemDefinition": build_business_system_definition(app_name, blueprint, consumer_osdk),
        "seed": seed_plan(identifier, blueprint),
        "ontologyResources": ontology_resources(blueprint, f"seed.{identifier}"),
        "applicationResources": application_resources(blueprint),
        "consumerOsdk": consumer_osdk,
        "react": {
            "routes": ["/apps/:applicationId", "/workshop/:applicationId"],
            "objectTypes": [row["apiName"] for row in records],
            "actionTypes": [row["apiName"] for row in actions],
            "functionTypes": [row["apiName"] for row in functions],
            "framework": "foundry_workshop_runtime",
        },
        "ci": {"commands": ["pnpm consumer-osdk:check", "pnpm typecheck", "pnpm test", "pnpm build"]},
        "requiredApprovals": ["pilot.application.generate"],
    }


def _items(value: object) -> list[JsonObject]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValidationFailed("Domain OS blueprint collection is invalid")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValidationFailed("Domain OS blueprint collection is invalid")
    return list(value)


def _pilot_slug(value: str) -> str:
    if not value.isascii():
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        return f"domain-os-{digest}"
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise FdePlatformToolError("schema_invalid", "applicationName must contain letters or numbers")
    return slug[:64]


def pilot_identifier(slug: str) -> str:
    value = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_")
    if not value or not value[0].isalpha():
        value = f"pilot_{value}"
    return value[:64]
