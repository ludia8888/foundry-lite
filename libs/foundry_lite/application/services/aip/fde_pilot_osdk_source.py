"""TypeScript source generation for a Domain OS Pilot application package."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError

JsonObject = Mapping[str, object]


def consumer_contract(plan: JsonObject) -> str:
    """Render the reviewed blueprint and typed app resources as a release contract."""

    blueprint = _blueprint(plan)
    boundary = _mapping(plan.get("consumerOsdk"), "consumerOsdk")
    value = {
        "schemaVersion": "foundry-lite-generated-consumer-osdk/v1",
        **boundary,
        "domainOsBlueprint": blueprint,
        "businessSystemDefinition": _business_system_definition(plan),
        "objects": _records(blueprint),
        "actions": _actions(blueprint),
        "functions": _functions(blueprint),
        "requiredGate": "quality:consumer-osdk",
    }
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def generated_source(plan: JsonObject) -> str:
    """Render all blueprint records and workflow actions as typed OSDK resources."""

    blueprint = _blueprint(plan)
    records = _records(blueprint)
    actions = _actions(blueprint)
    object_names = [str(row["apiName"]) for row in records]
    action_names = [str(row["apiName"]) for row in actions]
    functions = _functions(blueprint)
    function_names = [str(row["apiName"]) for row in functions]
    lines = [
        "// Generated application OSDK. Do not edit by hand.",
        "import type {",
        "  ActionApplyResponse,",
        "  FoundryLiteObject,",
        "  OsdkActionType,",
        "  OsdkFunctionType,",
        "  OsdkObjectType,",
        '} from "./runtime";',
        "",
    ]
    for record in records:
        lines.extend(_object_lines(record))
    for action in actions:
        lines.extend(_action_lines(action))
    for function in functions:
        lines.extend(_function_lines(function))
    manifest = _manifest(plan, object_names, action_names, function_names)
    lines.extend(
        [
            f"export const $Objects = {{ {', '.join(object_names)} }} as const;",
            f"export const $Actions = {{ {', '.join(action_names)} }} as const;",
            f"export const $Functions = {{ {', '.join(function_names)} }} as const;",
            f"export const CONSUMER_OSDK_MANIFEST = {json.dumps(manifest, sort_keys=True)} as const;",
            "",
        ]
    )
    return "\n".join(lines)


def react_hook_source(plan: JsonObject) -> str:
    """Render a domain hook; customer screens never receive the base client."""

    blueprint = _blueprint(plan)
    primary = _records(blueprint)[0]
    actions = _actions(blueprint)
    object_name = str(primary["apiName"])
    imports = [object_name, *(str(row["apiName"]) for row in actions)]
    type_imports = [f"type {object_name} as {object_name}Object"]
    action_type_imports = [f"type {row['apiName']}Params" for row in actions]
    lines = _react_hook_header([*imports, *type_imports, *action_type_imports])
    lines.extend(_react_hook_state_lines(object_name))
    for action in actions:
        lines.extend(_action_hook_lines(action))
    action_pairs = ", ".join(f"{_camel(str(row['apiName']))}: start{row['apiName']}" for row in actions)
    lines.extend([f"  return {{ items, error, isLoading, refresh, actions: {{ {action_pairs} }} }} as const;", "}", ""])
    return "\n".join(lines)


def _react_hook_header(imports: list[str]) -> list[str]:
    return [
        'import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState } from "react";',
        'import type { ReactNode } from "react";',
        'import { createBrowserFoundryLiteOsdkClient } from "./runtime";',
        'import type { FoundryLiteOsdkClient } from "./runtime";',
        f'import {{ {", ".join(imports)} }} from "./generated";',
        "",
        "const PilotOsdkContext = createContext<FoundryLiteOsdkClient | null>(null);",
        "export function PilotApplicationProvider({ children }: { children?: ReactNode }) {",
        "  const client = useMemo(() => createBrowserFoundryLiteOsdkClient(), []);",
        "  return createElement(PilotOsdkContext.Provider, { value: client }, children);",
        "}",
        "function usePilotOsdkClient() {",
        "  const client = useContext(PilotOsdkContext);",
        '  if (!client) throw new Error("업무 앱 연결을 찾지 못했습니다.");',
        "  return client;",
        "}",
        "",
    ]


def _react_hook_state_lines(object_name: str) -> list[str]:
    return [
        "export function usePilotApplicationScreen() {",
        "  const osdk = usePilotOsdkClient();",
        f"  const [items, setItems] = useState<readonly {object_name}Object[]>([]);",
        "  const [error, setError] = useState<Error | null>(null);",
        "  const [isLoading, setIsLoading] = useState(true);",
        "  const refresh = useCallback(async () => { setIsLoading(true); setError(null);",
        f"    try {{ const page = await osdk({object_name}).fetchPage({{ pageSize: 50 }}); setItems(page.data); }}",
        "    catch (reason: unknown) { setError(reason instanceof Error ? reason : new Error(String(reason))); }",
        "    finally { setIsLoading(false); } }, [osdk]);",
        "  useEffect(() => { void refresh(); }, [refresh]);",
    ]


def application_source(plan: JsonObject, package_name: str) -> str:
    """Render a calm, task-first customer app shell using only the app hook."""

    blueprint, title, summary, policies, screens, record_fields = _application_literals(plan)
    action_forms = "\n".join(_action_form_source(row) for row in _actions(blueprint))
    prelude = _application_prelude(package_name, title, summary, policies, screens, record_fields)
    return prelude + (
        "export default function App() { const screen = usePilotApplicationScreen(); "
        'const [message, setMessage] = useState(""); const [isRunning, setIsRunning] = useState(false); '
        "async function run(action: () => Promise<{ status: string }>) { if (isRunning) return; "
        'setIsRunning(true); setMessage("처리 중입니다…"); '
        'try { const result = await action(); if (result.status !== "succeeded") '
        'throw new Error("업무가 완료되지 않았습니다."); '
        'setMessage("업무를 완료했습니다."); await screen.refresh(); } catch (reason) { '
        'setMessage(reason instanceof Error ? reason.message : "업무를 처리하지 못했습니다."); '
        "} finally { setIsRunning(false); } } "
        "if (screen.isLoading) return <main><h1>{title}</h1><p>업무 기록을 불러오는 중입니다.</p></main>; "
        "if (screen.error) return <main><h1>{title}</h1>"
        "<p>기록을 불러오지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.</p></main>; "
        "return <main><header><p>업무 홈</p><h1>{title}</h1><p>{summary}</p></header>"
        '<nav aria-label="업무 화면">{screens.map((screen) => <span key={screen.id}>{screen.title}</span>)}</nav>'
        "<section><h2>지금 처리할 일</h2><p>{screen.items.length}건</p>"
        "{screen.items.length ? screen.items.map((item) => <article "
        "key={`${item.objectType}:${item.objectId}`}><h3>{String(item.properties.name ?? item.objectId)}</h3>"
        '<p>현재 상태: {String(item.properties.status ?? "확인 필요")}</p>'
        f'<div aria-label="업무 버튼">{action_forms}</div>'
        "<details><summary>업무 정보 자세히 보기</summary><dl>{recordFields.map((field) => "
        "<div key={field.apiName}><dt>{field.displayName}</dt>"
        '<dd>{String(item.properties[field.apiName] ?? "입력되지 않음")}</dd></div>)}</dl></details>'
        "</article>) : <p>처리할 업무가 없습니다.</p>}</section>"
        "<aside><h2>업무 규칙</h2><ul>{policies.map((policy) => <li key={policy.name}>"
        "<strong>{policy.name}</strong><p>{policy.statement}</p>"
        "<small>{policyLabels[policy.automationStatus]}</small></li>)}</ul></aside>"
        '{message ? <p role="status">{message}</p> : null}'
        "</main>; }\n"
    )


def _application_prelude(
    package_name: str,
    title: str,
    summary: str,
    policies: str,
    screens: str,
    record_fields: str,
) -> str:
    return (
        'import { useState } from "react";\n'
        f"import {{ usePilotApplicationScreen }} from {json.dumps(f'{package_name}/react')};\n"
        f"const title = {title};\nconst summary = {summary};\nconst policies = {policies} as const;\n"
        f"const recordFields = {record_fields} as const;\n"
        'const policyLabels = { executable_precondition: "조건 불충족 시 자동 차단", '
        'human_confirmation: "실행 전 사람이 확인", '
        'documented_for_review: "검토용 규칙 · 아직 자동화 안 됨" } as const;\n'
        f"const screens = {screens} as const;\n"
    )


def _application_literals(plan: JsonObject) -> tuple[dict[str, object], str, str, str, str, str]:
    blueprint = _blueprint(plan)
    primary = _records(blueprint)[0]
    policies = [
        {
            "name": row["name"],
            "statement": row["statement"],
            "automationStatus": row["automationStatus"],
        }
        for row in _mapping_items(blueprint.get("policies"), "domainOsBlueprint.policies")
    ]
    fields = [
        {"apiName": row["apiName"], "displayName": row["displayName"]}
        for row in _mapping_items(primary.get("fields"), "record.fields")
        if row["apiName"] not in {primary["primaryKey"], "name", "status"}
    ]
    experience = _mapping(
        _business_system_definition(plan).get("experience"),
        "businessSystemDefinition.experience",
    )
    workshop = _mapping(experience.get("workshopApp"), "businessSystemDefinition.experience.workshopApp")
    screens = [
        {"id": row["id"], "title": row["name"]}
        for row in _mapping_items(workshop.get("pages"), "businessSystemDefinition.experience.workshopApp.pages")
    ]
    return (
        blueprint,
        json.dumps(str(plan["applicationName"]), ensure_ascii=False),
        json.dumps(str(blueprint.get("summary") or ""), ensure_ascii=False),
        json.dumps(policies, ensure_ascii=False),
        json.dumps(screens, ensure_ascii=False),
        json.dumps(fields, ensure_ascii=False),
    )


def ontology_reexport(plan: JsonObject, package_name: str) -> str:
    """Export the primary object without rebuilding its descriptor in the screen."""

    primary = _records(_blueprint(plan))[0]
    return f"export {{ {primary['apiName']} as PilotObjectType }} from {json.dumps(package_name)};\n"


def _object_lines(record: JsonObject) -> list[str]:
    api_name = str(record["apiName"])
    fields = _mapping_items(record.get("fields"), "record.fields")
    type_lines = [f"export type {api_name}Properties = {{"]
    type_lines.extend(_field_line(field) for field in fields)
    type_lines.extend(["};", f'export type {api_name} = FoundryLiteObject<"{api_name}", {api_name}Properties>;'])
    descriptor = {
        "kind": "object",
        "apiName": api_name,
        "primaryKey": record["primaryKey"],
        "titleProperty": "name",
        "properties": [field["apiName"] for field in fields],
        "propertyDatasources": {},
    }
    type_lines.extend(
        [
            f"export const {api_name} = {json.dumps(descriptor, sort_keys=True)} "
            f"as const as OsdkObjectType<{api_name}>;",
            "",
        ]
    )
    return type_lines


def _action_lines(action: JsonObject) -> list[str]:
    api_name = str(action["apiName"])
    target = str(action["targetRecord"])
    parameters = _mapping_items(action.get("parameters"), "action.parameters")
    lines = [f"export type {api_name}Params = {{"]
    lines.extend(f"  readonly {value['apiName']}: string;" for value in parameters)
    lines.extend(
        [
            "};",
            f"export type {api_name}Request = {{",
            f'  readonly objectType?: "{target}";',
            "  readonly objectId: string;",
            "  readonly expectedObjectVersion: number;",
            f"  readonly params: {api_name}Params;",
            "  readonly idempotencyKey: string;",
            "};",
            f"export const {api_name} = {{",
            '  kind: "action",',
            f'  apiName: "{api_name}",',
            f'  targetObjectType: "{target}",',
            '  targetKind: "object",',
            f"}} as const as OsdkActionType<{api_name}Request, ActionApplyResponse>;",
            "",
        ]
    )
    return lines


def _function_lines(function: JsonObject) -> list[str]:
    api_name = str(function["apiName"])
    return [
        f"export type {api_name}Output = {{ readonly groups: readonly object[]; readonly totalGroups: number }};",
        f"export const {api_name} = {{",
        '  kind: "function",',
        f'  apiName: "{api_name}",',
        f"}} as const as OsdkFunctionType<Record<string, never>, {api_name}Output>;",
        "",
    ]


def _action_hook_lines(action: JsonObject) -> list[str]:
    api_name = str(action["apiName"])
    method_name = f"start{api_name}"
    return [
        f"  const {method_name} = useCallback((input: {{ objectId: string; expectedObjectVersion: number; ",
        f"    params: {api_name}Params; idempotencyKey: string }}) =>",
        f"    osdk({api_name}).startAction({{ objectId: input.objectId, "
        "expectedObjectVersion: input.expectedObjectVersion, params: input.params },",
        "      { idempotencyKey: input.idempotencyKey, waitSeconds: 30 }), [osdk]);",
    ]


def _action_form_source(action: JsonObject) -> str:
    api_name = str(action["apiName"])
    method_name = _camel(api_name)
    fields = _mapping_items(action.get("parameters"), "action.parameters")
    inputs = "".join(
        f'<label>{_jsx_text(str(value["displayName"]))}<input name="{value["apiName"]}" required /></label>'
        for value in fields
    )
    params = ", ".join(f'{value["apiName"]}: String(data.get("{value["apiName"]}") ?? "")' for value in fields)
    params_value = "{ " + params + " }" if params else "{}"
    label = _jsx_text(str(action["displayName"]))
    allowed_actors = " · ".join(_text_items(action.get("allowedActors"), "action.allowedActors"))
    permission = f'<p className="permission">실행할 수 있는 사람: {_jsx_text(allowed_actors)}</p>'
    approval = (
        '<label><input name="confirmation" type="checkbox" required />내용을 확인했고 이 업무를 실행합니다.</label>'
        if action.get("requiresApproval") is True
        else ""
    )
    states = json.dumps(action.get("fromStates") or [], ensure_ascii=False)
    return (
        f"{{{states}.includes(String(item.properties.status)) ? <form onSubmit={{(event) => {{ "
        "event.preventDefault(); const data = new FormData(event.currentTarget); "
        f"void run(() => screen.actions.{method_name}({{ objectId: item.objectId, "
        f"expectedObjectVersion: item.objectVersion, params: {params_value}, "
        f"idempotencyKey: `{api_name}:${{item.objectId}}:${{Date.now()}}` }})); }} }}>"
        f'<h3>{label}</h3>{permission}{inputs}{approval}<button type="submit" disabled={{isRunning}}>'
        f'{{isRunning ? "처리 중…" : "{label}"}}</button></form> : null}}'
    )


def _workflow_states(blueprint: JsonObject) -> list[str]:
    workflow = _mapping(blueprint.get("workflow"), "domainOsBlueprint.workflow")
    return _text_items(workflow.get("states"), "domainOsBlueprint.workflow.states")


def _jsx_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _field_line(field: JsonObject) -> str:
    api_name = str(field["apiName"])
    scalar = _typescript_scalar(str(field["type"]))
    optional = "" if field.get("required") is True else "?"
    nullable = "" if field.get("required") is True else " | null"
    return f"  readonly {api_name}{optional}: {scalar}{nullable};"


def _manifest(
    plan: JsonObject,
    object_names: list[str],
    action_names: list[str],
    function_names: list[str],
) -> dict[str, object]:
    boundary = _mapping(plan.get("consumerOsdk"), "consumerOsdk")
    return {
        "schemaVersion": "foundry-lite-consumer-osdk-manifest/v1",
        "applicationId": boundary["applicationId"],
        "profile": "consumer_osdk_strict",
        "objectApiNames": object_names,
        "actionApiNames": action_names,
        "functionApiNames": function_names,
        "businessSystemDefinitionFingerprint": _business_system_definition(plan)["definitionFingerprint"],
    }


def _business_system_definition(plan: JsonObject) -> dict[str, object]:
    return _mapping(plan.get("businessSystemDefinition"), "businessSystemDefinition")


def _blueprint(plan: JsonObject) -> dict[str, object]:
    return _mapping(plan.get("domainOsBlueprint"), "domainOsBlueprint")


def _records(blueprint: JsonObject) -> list[dict[str, object]]:
    records = _mapping_items(blueprint.get("records"), "domainOsBlueprint.records")
    if not records:
        raise FdePlatformToolError("schema_invalid", "Domain OS requires a primary record")
    return records


def _actions(blueprint: JsonObject) -> list[dict[str, object]]:
    workflow = _mapping(blueprint.get("workflow"), "domainOsBlueprint.workflow")
    return _mapping_items(workflow.get("actions"), "domainOsBlueprint.workflow.actions")


def _functions(blueprint: JsonObject) -> list[dict[str, object]]:
    return _mapping_items(blueprint.get("functions") or [], "domainOsBlueprint.functions")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an object")
    return {str(name): item for name, item in value.items()}


def _mapping_items(value: object, field: str) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a list")
    if not all(isinstance(item, Mapping) for item in value):
        raise FdePlatformToolError("schema_invalid", f"{field} must contain objects")
    return [{str(name): item for name, item in row.items()} for row in value if isinstance(row, Mapping)]


def _text_items(value: object, field: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", f"{field} must be a text list")
    return [str(item) for item in value]


def _camel(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9가-힣]+", value)
    pascal = "".join(word[:1].upper() + word[1:] for word in words) or "value"
    return pascal[:1].lower() + pascal[1:]


def _typescript_scalar(value: str) -> str:
    return {"boolean": "boolean", "float": "number", "integer": "number"}.get(value, "string")


__all__ = ["application_source", "consumer_contract", "generated_source", "ontology_reexport", "react_hook_source"]
