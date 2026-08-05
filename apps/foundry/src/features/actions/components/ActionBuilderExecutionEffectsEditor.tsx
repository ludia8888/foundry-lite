import type { ActionNotificationPolicy, OntologyCatalogFunction } from "@foundry-lite/sdk";
import { Plus, Trash2, Workflow } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  ACTION_BUILDER_EFFECT_KINDS,
  newActionBuilderEffect,
  newActionBuilderEffectPayloadEntry,
  newActionBuilderEffectResponseField,
  type ActionBuilderDraft,
  type ActionBuilderEffect,
} from "../lib/action-builder-model";

export function ActionBuilderExecutionEffectsEditor(props: {
  draft: ActionBuilderDraft;
  functions: OntologyCatalogFunction[];
  notificationPolicies: ActionNotificationPolicy[];
  actionTypes: Array<{ apiName: string }>;
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const changeMode = (executionMode: "rules" | "function") => props.onChange({
    ...props.draft,
    executionMode,
    riskLevel: executionMode === "function" ? "high" : props.draft.riskLevel,
  });
  const updateEffect = (key: string, next: ActionBuilderEffect) => props.onChange({
    ...props.draft,
    effects: props.draft.effects.map((effect) => effect.key === key ? next : effect),
  });
  const addEffect = () => props.onChange({
    ...props.draft,
    riskLevel: "high",
    effects: [...props.draft.effects, newActionBuilderEffect(props.draft.effects.length)],
  });
  return (
    <section className="space-y-4 rounded border bg-card p-3">
      <div>
        <h2 className="text-sm font-semibold">실행 엔진과 외부효과</h2>
        <p className="text-[11px] text-muted-foreground">규칙 기반 원자 편집 또는 version-pinned function 중 하나를 선택하고, 등록된 effect target만 호출합니다.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-[220px_minmax(0,1fr)]">
        <Field label="실행 방식"><Select value={props.draft.executionMode} onValueChange={(value) => changeMode(value as "rules" | "function")}><SelectTrigger aria-label="Action 실행 방식"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="rules">선언형 편집 규칙</SelectItem><SelectItem value="function" disabled={props.draft.targetKind === "interface"}>Version-pinned function</SelectItem></SelectContent></Select></Field>
        {props.draft.executionMode === "function" ? (
          <FunctionPicker draft={props.draft} functions={props.functions} onChange={props.onChange} />
        ) : (
          <div className="flex items-center gap-2 rounded bg-muted/40 px-3 text-[11px] text-muted-foreground"><Workflow className="size-4" />규칙은 아래에서 순서대로 구성하며 전체 EditPlan이 한 번에 커밋됩니다.</div>
        )}
      </div>
      <div className="space-y-3 border-t pt-3">
        <div className="flex items-start justify-between gap-3">
          <div><div className="text-[11px] font-medium">Governed side effects</div><p className="text-[10px] text-muted-foreground">URI를 직접 받지 않고 connector·notification·event·schedule 같은 등록된 target reference를 사용합니다.</p></div>
          <Button size="sm" variant="outline" onClick={addEffect}><Plus />외부효과</Button>
        </div>
        {props.draft.effects.map((effect, index) => (
          <EffectEditor
            key={effect.key}
            value={effect}
            index={index}
            onChange={(next) => updateEffect(effect.key, next)}
            onDelete={() => props.onChange({ ...props.draft, effects: props.draft.effects.filter((item) => item.key !== effect.key) })}
            notificationPolicies={props.notificationPolicies}
          />
        ))}
        {!props.draft.effects.length ? <p className="rounded border border-dashed p-3 text-center text-[10px] text-muted-foreground">외부효과가 없으면 Ontology 편집만 수행합니다.</p> : null}
      </div>
      <div className="grid gap-3 border-t pt-3 md:grid-cols-2">
        <div className="rounded border bg-muted/20 p-3">
          <div className="text-[11px] font-medium">Action Log · 항상 사용</div>
          <p className="mt-1 text-[10px] text-muted-foreground">
            제출마다 불변 로그 하나를 만들고 실행자·파라미터·편집 객체·effect receipt를 연결합니다.
          </p>
        </div>
        <div className="space-y-3 rounded border bg-muted/20 p-3 text-[11px]">
          <label className="flex items-start gap-2">
            <Checkbox
              aria-label="Action 되돌리기 허용"
              checked={props.draft.isRevertEnabled}
              onCheckedChange={(value) =>
                props.onChange({ ...props.draft, isRevertEnabled: value === true })
              }
            />
            <span>
              <span className="block font-medium">안전한 되돌리기 허용</span>
              <span className="mt-1 block text-[10px] text-muted-foreground">
                원 실행자이고 영향 객체의 최신 편집일 때만 전체 내부 편집을 원자적으로 복구합니다. 외부효과는 다시 호출하지 않습니다.
              </span>
            </span>
          </label>
          {props.draft.isRevertEnabled && props.draft.effects.length ? (
            <Field label="외부효과 보상 Action">
              <Select
                value={props.draft.compensationActionApiName || "__none__"}
                onValueChange={(value) => props.onChange({
                  ...props.draft,
                  compensationActionApiName: value === "__none__" ? "" : value,
                })}
              >
                <SelectTrigger aria-label="외부효과 보상 Action"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">지정하지 않음 · 운영자 조정 필요</SelectItem>
                  {props.actionTypes
                    .filter((item) => item.apiName !== props.draft.apiName)
                    .map((item) => <SelectItem key={item.apiName} value={item.apiName}>{item.apiName}</SelectItem>)}
                </SelectContent>
              </Select>
              <p className="text-[10px] text-muted-foreground">
                Ontology revert 뒤에도 이미 전달된 webhook·알림은 보존됩니다. 필요하면 별도 승인·로그를 남기는 보상 Action을 안내합니다.
              </p>
            </Field>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function FunctionPicker(props: {
  draft: ActionBuilderDraft;
  functions: OntologyCatalogFunction[];
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const value = props.draft.functionApiName && props.draft.functionVersion
    ? functionCoordinate(props.draft.functionApiName, props.draft.functionVersion) : undefined;
  const selectFunction = (coordinate: string) => {
    const selected = props.functions.find((item) => functionCoordinate(item.apiName, item.version) === coordinate);
    if (!selected) return;
    props.onChange({
      ...props.draft,
      functionApiName: selected.apiName,
      functionVersion: selected.version,
      riskLevel: "high",
    });
  };
  const changeExecutionMode = (executionMode: "per_request" | "batched") => props.onChange({
    ...props.draft,
    functionExecutionMode: executionMode,
    functionBatchInputName: executionMode === "batched" ? props.draft.functionBatchInputName || "requests" : "",
    functionMaxBatchSize: executionMode === "batched" ? 10_000 : 20,
  });
  return (
    <div className="space-y-3 rounded border bg-muted/20 p-3">
      <div className="grid gap-2 md:grid-cols-2">
        <Field label="고정된 함수 버전">
          <Select value={value} onValueChange={selectFunction}>
            <SelectTrigger aria-label="Action version-pinned function"><SelectValue placeholder="함수와 버전 선택" /></SelectTrigger>
            <SelectContent>{props.functions.map((item) => <SelectItem key={functionCoordinate(item.apiName, item.version)} value={functionCoordinate(item.apiName, item.version)}>{item.displayName ?? item.apiName} · {item.apiName}@{item.version}</SelectItem>)}</SelectContent>
          </Select>
        </Field>
        <Field label="함수 배치 실행">
          <Select value={props.draft.functionExecutionMode} onValueChange={(mode) => changeExecutionMode(mode as "per_request" | "batched")}>
            <SelectTrigger aria-label="Action function batch execution mode"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="per_request">요청별 순차 실행 · 최대 20</SelectItem><SelectItem value="batched">List of structs 한 번 실행 · 최대 10,000</SelectItem></SelectContent>
          </Select>
        </Field>
      </div>
      <div className="grid gap-2 md:grid-cols-2">
        {props.draft.functionExecutionMode === "batched" ? <Field label="List-of-struct input name"><Input aria-label="Action function batch input name" value={props.draft.functionBatchInputName} onChange={(event) => props.onChange({ ...props.draft, functionBatchInputName: event.target.value })} placeholder="requests" /></Field> : <div className="rounded border border-dashed p-2 text-[10px] text-muted-foreground">여러 요청을 순서대로 계산한 뒤, 모든 OntologyEditBatch를 한 트랜잭션으로 커밋합니다.</div>}
        <Field label="한 실행의 최대 요청 수"><Input aria-label="Action function maximum batch size" type="number" min={1} max={props.draft.functionExecutionMode === "batched" ? 10_000 : 20} value={props.draft.functionMaxBatchSize} onChange={(event) => props.onChange({ ...props.draft, functionMaxBatchSize: Number(event.target.value) })} /></Field>
      </div>
      <p className="text-[10px] text-muted-foreground">Batched 모드는 함수 입력을 정확히 하나의 required list-of-struct로 고정합니다. 어느 계산이나 OCC 검증이 실패해도 전체 Ontology 편집은 커밋되지 않습니다.</p>
    </div>
  );
}

function EffectEditor(props: {
  value: ActionBuilderEffect;
  index: number;
  onChange: (effect: ActionBuilderEffect) => void;
  onDelete: () => void;
  notificationPolicies: ActionNotificationPolicy[];
}) {
  const update = (values: Partial<ActionBuilderEffect>) => props.onChange({ ...props.value, ...values });
  const changePhase = (phase: "before_commit" | "after_commit") => update({
    phase,
    kind: phase === "before_commit" ? "webhook" : props.value.kind,
    maxAttempts: phase === "before_commit" ? 1 : Math.max(3, props.value.maxAttempts),
  });
  const addPayload = () => update({ payload: [...props.value.payload, newActionBuilderEffectPayloadEntry(props.value.payload.length)] });
  const addResponseField = () => update({ responseFields: [...props.value.responseFields, newActionBuilderEffectResponseField(props.value.responseFields.length)] });
  return (
    <div className="space-y-3 rounded border bg-muted/20 p-3">
      <div className="grid items-end gap-2 md:grid-cols-[minmax(0,1fr)_170px_180px_auto]">
        <Field label="effect ID"><Input aria-label={`외부효과 ${props.index + 1} ID`} value={props.value.effectId} onChange={(event) => update({ effectId: event.target.value })} /></Field>
        <Field label="단계"><Select value={props.value.phase} onValueChange={(phase) => changePhase(phase as "before_commit" | "after_commit")}><SelectTrigger aria-label={`외부효과 ${props.index + 1} 단계`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="before_commit">커밋 전 writeback</SelectItem><SelectItem value="after_commit">커밋 후 전달</SelectItem></SelectContent></Select></Field>
        <Field label="종류"><Select value={props.value.kind} onValueChange={(kind) => update({ kind })} disabled={props.value.phase === "before_commit"}><SelectTrigger aria-label={`외부효과 ${props.index + 1} 종류`}><SelectValue /></SelectTrigger><SelectContent>{ACTION_BUILDER_EFFECT_KINDS.map((kind) => <SelectItem key={kind} value={kind}>{effectKindLabel(kind)}</SelectItem>)}</SelectContent></Select></Field>
        <Button size="icon-sm" variant="ghost" aria-label={`외부효과 ${props.index + 1} 삭제`} onClick={props.onDelete}><Trash2 /></Button>
      </div>
      <EffectTargetField {...props} onTargetChange={(targetRef) => update({ targetRef })} />
      <div className="grid gap-2 sm:grid-cols-2">
        <Field label="최대 시도"><Input aria-label={`외부효과 ${props.index + 1} 최대 시도`} type="number" min={1} max={10} value={props.value.maxAttempts} onChange={(event) => update({ maxAttempts: Number(event.target.value) })} disabled={props.value.phase === "before_commit"} /></Field>
        <Field label="Timeout (초)"><Input aria-label={`외부효과 ${props.index + 1} timeout`} type="number" min={1} max={300} value={props.value.timeoutSeconds} onChange={(event) => update({ timeoutSeconds: Number(event.target.value) })} /></Field>
      </div>
      <div className="space-y-2 border-t pt-2">
        <div className="flex items-center justify-between"><span className="text-[10px] font-medium">Payload fields</span><Button size="sm" variant="ghost" onClick={addPayload}><Plus />필드</Button></div>
        {props.value.payload.map((entry) => (
          <div key={entry.key} className="grid grid-cols-[minmax(120px,0.7fr)_minmax(0,1fr)_auto] gap-2">
            <Input aria-label="외부효과 payload field" value={entry.name} onChange={(event) => update({ payload: props.value.payload.map((item) => item.key === entry.key ? { ...item, name: event.target.value } : item) })} placeholder="template" />
            <Input aria-label="외부효과 payload value" value={entry.value} onChange={(event) => update({ payload: props.value.payload.map((item) => item.key === entry.key ? { ...item, value: event.target.value } : item) })} placeholder="문자열 또는 JSON 값" />
            <Button size="icon-sm" variant="ghost" aria-label="외부효과 payload field 삭제" onClick={() => update({ payload: props.value.payload.filter((item) => item.key !== entry.key) })}><Trash2 /></Button>
          </div>
        ))}
        {props.value.kind === "notification" ? <p className="text-[10px] text-muted-foreground">알림 문구에는 {"{{object.status}}"}, {"{{parameters.reason}}"}, {"{{actor.userId}}"}, {"{{action.runId}}"} 형식을 사용할 수 있습니다. 값은 Ontology 편집 직전에 고정됩니다.</p> : null}
      </div>
      {props.value.phase === "before_commit" ? (
        <div className="space-y-2 border-t pt-2">
          <div className="flex items-center justify-between"><span className="text-[10px] font-medium">Typed response fields</span><Button size="sm" variant="ghost" onClick={addResponseField}><Plus />응답 필드</Button></div>
          {props.value.responseFields.map((entry) => (
            <div key={entry.key} className="grid grid-cols-[minmax(120px,1fr)_180px_auto] gap-2">
              <Input aria-label="외부효과 response field" value={entry.name} onChange={(event) => update({ responseFields: props.value.responseFields.map((item) => item.key === entry.key ? { ...item, name: event.target.value } : item) })} placeholder="approvalCode" />
              <Select value={entry.dataType} onValueChange={(dataType) => update({ responseFields: props.value.responseFields.map((item) => item.key === entry.key ? { ...item, dataType } : item) })}>
                <SelectTrigger aria-label={`${entry.name || "응답"} response type`}><SelectValue /></SelectTrigger>
                <SelectContent>{["string", "boolean", "integer", "long", "float", "decimal", "date", "timestamp"].map((dataType) => <SelectItem key={dataType} value={dataType}>{dataType}</SelectItem>)}</SelectContent>
              </Select>
              <Button size="icon-sm" variant="ghost" aria-label="외부효과 response field 삭제" onClick={() => update({ responseFields: props.value.responseFields.filter((item) => item.key !== entry.key) })}><Trash2 /></Button>
            </div>
          ))}
          <p className="text-[10px] text-muted-foreground">선언된 필드만 규칙의 writeback 응답 값으로 사용할 수 있고, 타입이 다르면 Ontology 커밋 전에 차단됩니다.</p>
        </div>
      ) : null}
      <p className="text-[10px] text-muted-foreground">{props.value.phase === "before_commit" ? "응답이 모호하면 자동 재호출하지 않고 outcome_unknown으로 닫습니다." : "Ontology 커밋 후 outbox로 전달하며 실패해도 이미 커밋한 객체 편집은 보존합니다."}</p>
    </div>
  );
}

function EffectTargetField(props: {
  value: ActionBuilderEffect;
  index: number;
  notificationPolicies: ActionNotificationPolicy[];
  onTargetChange: (targetRef: string) => void;
}) {
  if (props.value.kind !== "notification") return <Field label="등록된 target reference"><Input aria-label={`외부효과 ${props.index + 1} target reference`} value={props.value.targetRef} onChange={(event) => props.onTargetChange(event.target.value)} placeholder="connector:booking-partner 또는 topic:orders" /></Field>;
  return <Field label="등록된 알림 정책"><Select value={props.value.targetRef || undefined} onValueChange={props.onTargetChange}><SelectTrigger aria-label={`외부효과 ${props.index + 1} target reference`}><SelectValue placeholder="활성 알림 정책 선택" /></SelectTrigger><SelectContent>{props.notificationPolicies.map((policy) => <SelectItem key={policy.id} value={policy.targetRef}>{policy.displayName} · {policy.recipients.length}명</SelectItem>)}</SelectContent></Select>{!props.notificationPolicies.length ? <p className="text-[10px] text-destructive">먼저 알림 정책 탭에서 활성 정책을 만드세요.</p> : null}</Field>;
}

function functionCoordinate(apiName: string, version: string): string { return `${apiName}\u0000${version}`; }

function effectKindLabel(kind: string): string {
  return ({ webhook: "Webhook", notification: "Notification", event: "Event", schedule_build: "Schedule / Build", connector_command: "Connector command" } as Record<string, string>)[kind] ?? kind;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1"><Label className="text-[10px]">{label}</Label>{children}</div>;
}
