import { Braces, Plus, Trash2 } from "lucide-react";

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
  ACTION_BUILDER_CONDITION_OPERATORS,
  emptyActionBuilderDefault,
  newActionBuilderCondition,
  newActionBuilderOverride,
  type ActionBuilderCondition,
  type ActionBuilderConditionValue,
  type ActionBuilderDefault,
  type ActionBuilderDefaultKind,
  type ActionBuilderDraft,
  type ActionBuilderLinkedPropertyOption,
  type ActionBuilderOverride,
  type ActionBuilderParameter,
  type ActionBuilderPropertyOption,
} from "../lib/action-builder-model";
import type { ActionBuilderConstraints } from "../lib/action-builder-constraint-model";

type ObjectProperties = ActionBuilderPropertyOption[];

export function ActionBuilderParameterPolicyEditor(props: {
  parameter: ActionBuilderParameter;
  earlierParameters: string[];
  properties: ObjectProperties;
  onChange: (parameter: ActionBuilderParameter) => void;
}) {
  const updateOverride = (key: string, next: ActionBuilderOverride) => {
    props.onChange({
      ...props.parameter,
      overrides: props.parameter.overrides.map((item) => item.key === key ? next : item),
    });
  };
  const addOverride = () => props.onChange({
    ...props.parameter,
    overrides: [...props.parameter.overrides, newActionBuilderOverride(props.parameter.overrides.length)],
  });
  return (
    <details className="rounded border border-dashed bg-background/70 p-2">
      <summary className="cursor-pointer text-[11px] font-medium">기본값 · 조건부 override</summary>
      <div className="mt-3 space-y-3">
        <ConstraintEditor
          label={`${props.parameter.apiName || "파라미터"} 제약조건`}
          dataType={props.parameter.dataType}
          value={props.parameter.constraints}
          onChange={(constraints) => props.onChange({ ...props.parameter, constraints })}
        />
        <DefaultEditor
          label={`${props.parameter.apiName || "파라미터"} 기본값`}
          value={props.parameter.defaultValue}
          earlierParameters={props.earlierParameters}
          properties={props.properties}
          onChange={(defaultValue) => props.onChange({ ...props.parameter, defaultValue })}
        />
        <div className="space-y-2 border-t pt-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[11px] font-medium">조건부 override</div>
              <p className="text-[10px] text-muted-foreground">위에서 아래로 검사하고 첫 번째 일치 규칙만 적용합니다.</p>
            </div>
            <Button size="sm" variant="outline" onClick={addOverride} disabled={!props.earlierParameters.length}>
              <Plus />override
            </Button>
          </div>
          {!props.earlierParameters.length ? (
            <p className="rounded bg-muted/40 p-2 text-[10px] text-muted-foreground">첫 파라미터는 앞선 입력이 없어서 override 조건을 만들 수 없습니다.</p>
          ) : null}
          {props.parameter.overrides.map((override, index) => (
            <OverrideEditor
              key={override.key}
              index={index}
              value={override}
              earlierParameters={props.earlierParameters}
              properties={props.properties}
              dataType={props.parameter.dataType}
              onChange={(next) => updateOverride(override.key, next)}
              onDelete={() => props.onChange({
                ...props.parameter,
                overrides: props.parameter.overrides.filter((item) => item.key !== override.key),
              })}
            />
          ))}
        </div>
      </div>
    </details>
  );
}

export function ActionBuilderSubmissionCriteriaEditor(props: {
  draft: ActionBuilderDraft;
  properties: ObjectProperties;
  linkedProperties: ActionBuilderLinkedPropertyOption[];
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const parameters = props.draft.parameters.map((parameter) => parameter.apiName).filter(Boolean);
  const enable = () => props.onChange({
    ...props.draft,
    submissionCriteria: newActionBuilderCondition("submission-root"),
  });
  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">제출 조건</h2>
          <p className="text-[11px] text-muted-foreground">현재 객체·입력·사용자 조건을 통과한 요청만 계획과 실행으로 보냅니다.</p>
        </div>
        {props.draft.submissionCriteria ? (
          <Button size="sm" variant="ghost" onClick={() => props.onChange({ ...props.draft, submissionCriteria: null, submissionMessage: "" })}>
            <Trash2 />조건 제거
          </Button>
        ) : (
          <Button size="sm" variant="outline" onClick={enable}><Plus />조건 추가</Button>
        )}
      </div>
      {props.draft.submissionCriteria ? (
        <div className="space-y-3">
          <ConditionNodeEditor
            value={props.draft.submissionCriteria}
            parameters={parameters}
            properties={props.properties}
            linkedProperties={props.linkedProperties}
            onChange={(submissionCriteria) => props.onChange({ ...props.draft, submissionCriteria })}
          />
          <Field label="조건 불충족 안내">
            <Input
              aria-label="제출 조건 불충족 안내"
              value={props.draft.submissionMessage}
              onChange={(event) => props.onChange({ ...props.draft, submissionMessage: event.target.value })}
              placeholder="예: 대기 또는 검토 상태의 주문만 처리할 수 있습니다."
            />
          </Field>
        </div>
      ) : (
        <p className="rounded border border-dashed p-4 text-center text-[11px] text-muted-foreground">조건이 없으면 권한·위험 정책을 통과한 모든 대상 객체에 제출할 수 있습니다.</p>
      )}
    </section>
  );
}

function OverrideEditor(props: {
  index: number;
  value: ActionBuilderOverride;
  earlierParameters: string[];
  properties: ObjectProperties;
  dataType: string;
  onChange: (value: ActionBuilderOverride) => void;
  onDelete: () => void;
}) {
  const update = (values: Partial<ActionBuilderOverride>) => props.onChange({ ...props.value, ...values });
  return (
    <div className="space-y-3 rounded border bg-muted/20 p-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-muted-foreground">override {props.index + 1}</span>
        <Button size="icon-sm" variant="ghost" aria-label={`override ${props.index + 1} 삭제`} onClick={props.onDelete}><Trash2 /></Button>
      </div>
      <ConditionNodeEditor
        value={props.value.condition}
        parameters={props.earlierParameters}
        properties={props.properties}
        onChange={(condition) => update({ condition })}
      />
      <div className="grid gap-2 sm:grid-cols-3">
        <TriStateField label="필수" value={props.value.required} onChange={(required) => update({ required })} />
        <TriStateField label="표시" value={props.value.visible} onChange={(visible) => update({ visible })} />
        <TriStateField label="편집" value={props.value.editable} onChange={(editable) => update({ editable })} />
      </div>
      <DefaultEditor
        label="일치 시 기본값"
        value={props.value.defaultValue}
        earlierParameters={props.earlierParameters}
        properties={props.properties}
        onChange={(defaultValue) => update({ defaultValue })}
      />
      <label className="flex items-center gap-2 text-[11px]">
        <Checkbox
          aria-label={`override ${props.index + 1} 제약조건 변경`}
          checked={props.value.isConstraintsOverridden}
          onCheckedChange={(checked) => update({ isConstraintsOverridden: checked === true })}
        />
        일치 시 제약조건 변경
      </label>
      {props.value.isConstraintsOverridden ? (
        <ConstraintEditor
          label={`override ${props.index + 1} 제약조건`}
          dataType={props.dataType}
          value={props.value.constraints}
          onChange={(constraints) => update({ constraints })}
        />
      ) : null}
    </div>
  );
}

function ConstraintEditor(props: {
  label: string;
  dataType: string;
  value: ActionBuilderConstraints;
  onChange: (value: ActionBuilderConstraints) => void;
}) {
  const update = (values: Partial<ActionBuilderConstraints>) => props.onChange({ ...props.value, ...values });
  return (
    <div className="space-y-2 rounded border border-dashed bg-background/60 p-2">
      <div className="text-[10px] font-medium">{props.label}</div>
      <Field label="선택 가능 값 (쉼표 구분)">
        <Input aria-label={`${props.label} enum`} value={props.value.enumValues} onChange={(event) => update({ enumValues: event.target.value })} placeholder="예: standard, urgent" />
      </Field>
      {props.dataType === "string" ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="최소 길이"><Input aria-label={`${props.label} 최소 길이`} type="number" min={0} value={props.value.minLength} onChange={(event) => update({ minLength: event.target.value })} /></Field>
          <Field label="최대 길이"><Input aria-label={`${props.label} 최대 길이`} type="number" min={0} value={props.value.maxLength} onChange={(event) => update({ maxLength: event.target.value })} /></Field>
        </div>
      ) : null}
      {["integer", "long", "float", "decimal"].includes(props.dataType) ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="최솟값"><Input aria-label={`${props.label} 최솟값`} type="number" value={props.value.minimum} onChange={(event) => update({ minimum: event.target.value })} /></Field>
          <Field label="최댓값"><Input aria-label={`${props.label} 최댓값`} type="number" value={props.value.maximum} onChange={(event) => update({ maximum: event.target.value })} /></Field>
        </div>
      ) : null}
      {["array", "objectSet"].includes(props.dataType) ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <Field label="최소 항목 수"><Input aria-label={`${props.label} 최소 항목 수`} type="number" min={0} value={props.value.minItems} onChange={(event) => update({ minItems: event.target.value })} /></Field>
          <Field label="최대 항목 수"><Input aria-label={`${props.label} 최대 항목 수`} type="number" min={0} value={props.value.maxItems} onChange={(event) => update({ maxItems: event.target.value })} /></Field>
        </div>
      ) : null}
    </div>
  );
}

export function ConditionNodeEditor(props: {
  value: ActionBuilderCondition;
  parameters: string[];
  properties: ObjectProperties;
  linkedProperties?: ActionBuilderLinkedPropertyOption[];
  isParameterOnly?: boolean;
  onChange: (value: ActionBuilderCondition) => void;
}) {
  const replaceNode = (nodeType: "comparison" | "group" | "not") => {
    if (nodeType === "comparison") props.onChange(newActionBuilderCondition(`comparison-${Date.now()}`));
    if (nodeType === "group") props.onChange({ key: `group-${Date.now()}`, nodeType: "group", combinator: "all", children: [newActionBuilderCondition(`group-child-${Date.now()}`)] });
    if (nodeType === "not") props.onChange({ key: `not-${Date.now()}`, nodeType: "not", child: newActionBuilderCondition(`not-child-${Date.now()}`) });
  };
  return (
    <div className="space-y-2 rounded border-l-2 border-l-primary/40 bg-background p-2">
      <div className="flex items-center gap-2">
        <Braces className="size-3.5 text-primary" />
        <Select value={props.value.nodeType} onValueChange={(value) => replaceNode(value as "comparison" | "group" | "not")}>
          <SelectTrigger className="h-8 w-36 text-xs" aria-label="조건 노드 타입"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="comparison">비교</SelectItem><SelectItem value="group">all / any</SelectItem><SelectItem value="not">not</SelectItem></SelectContent>
        </Select>
      </div>
      {props.value.nodeType === "comparison" ? (
        <ComparisonEditor {...props} value={props.value} />
      ) : props.value.nodeType === "not" ? (
        <ConditionNodeEditor
          {...props}
          value={props.value.child}
          onChange={(child) => props.onChange({ key: props.value.key, nodeType: "not", child })}
        />
      ) : (
        <GroupEditor {...props} value={props.value} />
      )}
    </div>
  );
}

function ComparisonEditor(props: {
  value: Extract<ActionBuilderCondition, { nodeType: "comparison" }>;
  parameters: string[];
  properties: ObjectProperties;
  linkedProperties?: ActionBuilderLinkedPropertyOption[];
  isParameterOnly?: boolean;
  onChange: (value: ActionBuilderCondition) => void;
}) {
  const update = (values: Partial<typeof props.value>) => props.onChange({ ...props.value, ...values });
  return (
    <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_100px_minmax(0,1fr)]">
      <ConditionValueEditor label="왼쪽 조건 값" value={props.value.left} parameters={props.parameters} properties={props.properties} linkedProperties={props.linkedProperties} onChange={(left) => update({ left })} />
      <Field label="연산자"><Select value={props.value.operator} onValueChange={(operator) => update({ operator })}><SelectTrigger aria-label="조건 연산자"><SelectValue /></SelectTrigger><SelectContent>{ACTION_BUILDER_CONDITION_OPERATORS.map((operator) => <SelectItem key={operator} value={operator}>{operator}</SelectItem>)}</SelectContent></Select></Field>
      {props.value.operator !== "exists" ? <ConditionValueEditor label="오른쪽 조건 값" value={props.value.right} parameters={props.parameters} properties={props.properties} linkedProperties={props.linkedProperties} onChange={(right) => update({ right })} /> : <div className="self-end rounded bg-muted/40 p-2 text-[10px] text-muted-foreground">값의 존재 여부만 검사</div>}
    </div>
  );
}

function GroupEditor(props: {
  value: Extract<ActionBuilderCondition, { nodeType: "group" }>;
  parameters: string[];
  properties: ObjectProperties;
  linkedProperties?: ActionBuilderLinkedPropertyOption[];
  isParameterOnly?: boolean;
  onChange: (value: ActionBuilderCondition) => void;
}) {
  const updateChild = (key: string, child: ActionBuilderCondition) => props.onChange({ ...props.value, children: props.value.children.map((item) => item.key === key ? child : item) });
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <Select value={props.value.combinator} onValueChange={(combinator) => props.onChange({ ...props.value, combinator: combinator as "all" | "any" })}><SelectTrigger className="h-8 w-28 text-xs" aria-label="조건 그룹 방식"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">모두 충족</SelectItem><SelectItem value="any">하나 이상</SelectItem></SelectContent></Select>
        <Button size="sm" variant="outline" onClick={() => props.onChange({ ...props.value, children: [...props.value.children, newActionBuilderCondition(`group-${Date.now()}`)] })}><Plus />하위 조건</Button>
      </div>
      {props.value.children.map((child) => (
        <div key={child.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-1">
          <ConditionNodeEditor value={child} parameters={props.parameters} properties={props.properties} linkedProperties={props.linkedProperties} onChange={(next) => updateChild(child.key, next)} />
          <Button size="icon-sm" variant="ghost" aria-label="하위 조건 삭제" onClick={() => props.onChange({ ...props.value, children: props.value.children.filter((item) => item.key !== child.key) })}><Trash2 /></Button>
        </div>
      ))}
    </div>
  );
}

function ConditionValueEditor(props: {
  label: string;
  value: ActionBuilderConditionValue;
  parameters: string[];
  properties: ObjectProperties;
  linkedProperties?: ActionBuilderLinkedPropertyOption[];
  isParameterOnly?: boolean;
  onChange: (value: ActionBuilderConditionValue) => void;
}) {
  const updateKind = (kind: ActionBuilderConditionValue["kind"]) => props.onChange({ kind, value: "" });
  return (
    <div className="grid gap-1 sm:grid-cols-[120px_minmax(0,1fr)]">
      <Field label={`${props.label} 출처`}><Select value={props.value.kind} onValueChange={(kind) => updateKind(kind as ActionBuilderConditionValue["kind"])}><SelectTrigger aria-label={`${props.label} 출처`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="parameter">파라미터</SelectItem>{props.isParameterOnly ? null : <><SelectItem value="objectProperty">객체 속성</SelectItem>{props.linkedProperties?.length ? <SelectItem value="linkedObjectProperty">연결 객체 속성</SelectItem> : null}<SelectItem value="currentUser">현재 사용자</SelectItem></>}<SelectItem value="literal">고정 값</SelectItem></SelectContent></Select></Field>
      <Field label={props.label}><ConditionReferenceInput {...props} /></Field>
    </div>
  );
}

function ConditionReferenceInput(props: Parameters<typeof ConditionValueEditor>[0]) {
  const setValue = (value: string) => props.onChange({ ...props.value, value });
  if (props.value.kind === "parameter") return <OptionSelect label={props.label} value={props.value.value} options={props.parameters.map((value) => ({ value, label: value }))} onChange={setValue} />;
  if (props.value.kind === "objectProperty") return <OptionSelect label={props.label} value={props.value.value} options={props.properties.map((item) => ({ value: item.apiName, label: `${item.displayName} · ${item.apiName}` }))} onChange={setValue} />;
  if (props.value.kind === "linkedObjectProperty") return <LinkedConditionReferenceInput {...props} />;
  if (props.value.kind === "currentUser") return <Input aria-label={props.label} value={props.value.value || "id"} onChange={(event) => setValue(event.target.value)} placeholder="id, groups 또는 IdP 속성 API name" />;
  return <Input aria-label={props.label} value={props.value.value} onChange={(event) => setValue(event.target.value)} placeholder='문자열 또는 JSON: true, 10, ["ops"]' />;
}

function LinkedConditionReferenceInput(props: Parameters<typeof ConditionValueEditor>[0]) {
  const options = props.linkedProperties ?? [];
  const coordinate = `${props.value.linkedDirection ?? "outgoing"}:${props.value.value}:${props.value.linkedProperty ?? ""}`;
  const selectReference = (key: string) => {
    const selected = options.find((item) => item.key === key);
    if (!selected) return;
    props.onChange({
      ...props.value,
      value: selected.linkType,
      linkedDirection: selected.direction,
      linkedProperty: selected.property,
      linkedAggregation: props.value.linkedAggregation ?? "values",
    });
  };
  return (
    <div className="min-w-0 space-y-1">
      <OptionSelect
        label={`${props.label} 연결 속성`}
        value={coordinate}
        options={options.map((item) => ({ value: item.key, label: item.label }))}
        onChange={selectReference}
      />
      <OptionSelect
        label={`${props.label} 집계`}
        value={props.value.linkedAggregation ?? "values"}
        options={[{ value: "values", label: "값 목록" }, { value: "count", label: "개수" }]}
        onChange={(linkedAggregation) => props.onChange({
          ...props.value,
          linkedAggregation: linkedAggregation as "values" | "count",
        })}
      />
    </div>
  );
}

function DefaultEditor(props: {
  label: string;
  value: ActionBuilderDefault;
  earlierParameters: string[];
  properties: ObjectProperties;
  onChange: (value: ActionBuilderDefault) => void;
}) {
  const updateKind = (kind: ActionBuilderDefaultKind) => props.onChange({ ...emptyActionBuilderDefault(), kind });
  return (
    <div className="grid gap-2 md:grid-cols-[180px_minmax(0,1fr)]">
      <Field label={props.label}><Select value={props.value.kind} onValueChange={(kind) => updateKind(kind as ActionBuilderDefaultKind)}><SelectTrigger aria-label={`${props.label} 종류`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="none">없음</SelectItem><SelectItem value="literal">고정 값</SelectItem><SelectItem value="parameter">앞선 파라미터</SelectItem><SelectItem value="objectProperty">객체 속성</SelectItem><SelectItem value="currentUser">현재 사용자</SelectItem><SelectItem value="currentTime">현재 시간</SelectItem><SelectItem value="generatedId">생성 ID</SelectItem></SelectContent></Select></Field>
      {props.value.kind === "none" ? null : <Field label="기본값 원천"><DefaultReferenceInput {...props} /></Field>}
    </div>
  );
}

function DefaultReferenceInput(props: Parameters<typeof DefaultEditor>[0]) {
  const setReference = (reference: string) => props.onChange({ ...props.value, reference });
  if (props.value.kind === "literal") return <Input aria-label={`${props.label} 값`} value={props.value.value} onChange={(event) => props.onChange({ ...props.value, value: event.target.value })} placeholder="문자열 또는 JSON 값" />;
  if (props.value.kind === "parameter") return <OptionSelect label={`${props.label} 참조`} value={props.value.reference} options={props.earlierParameters.map((value) => ({ value, label: value }))} onChange={setReference} />;
  if (props.value.kind === "objectProperty") return <OptionSelect label={`${props.label} 참조`} value={props.value.reference} options={props.properties.map((item) => ({ value: item.apiName, label: `${item.displayName} · ${item.apiName}` }))} onChange={setReference} />;
  if (props.value.kind === "currentUser") return <Input aria-label={`${props.label} 참조`} value={props.value.reference || "id"} onChange={(event) => setReference(event.target.value)} placeholder="id, groups 또는 IdP 속성 API name" />;
  if (props.value.kind === "currentTime") return <OptionSelect label={`${props.label} 참조`} value={props.value.reference || "timestamp"} options={[{ value: "timestamp", label: "타임스탬프" }, { value: "date", label: "날짜" }]} onChange={setReference} />;
  return <OptionSelect label={`${props.label} 참조`} value={props.value.reference || "uuid"} options={[{ value: "uuid", label: "UUID" }]} onChange={setReference} />;
}

function TriStateField(props: { label: string; value: "inherit" | "true" | "false"; onChange: (value: "inherit" | "true" | "false") => void }) {
  return <Field label={props.label}><Select value={props.value} onValueChange={(value) => props.onChange(value as "inherit" | "true" | "false")}><SelectTrigger aria-label={`override ${props.label}`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="inherit">기본 설정 유지</SelectItem><SelectItem value="true">예</SelectItem><SelectItem value="false">아니요</SelectItem></SelectContent></Select></Field>;
}

function OptionSelect(props: { label: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return <Select value={props.value || undefined} onValueChange={props.onChange}><SelectTrigger className="w-full min-w-0" aria-label={props.label}><SelectValue placeholder="선택" /></SelectTrigger><SelectContent>{props.options.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent></Select>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1"><Label className="text-[10px]">{label}</Label>{children}</div>;
}
