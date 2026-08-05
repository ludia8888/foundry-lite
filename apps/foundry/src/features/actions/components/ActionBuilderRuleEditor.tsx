import type { OntologyCatalogInterface, OntologyCatalogLink } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import { ArrowRight, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
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
  ACTION_BUILDER_RULE_KINDS,
  ACTION_BUILDER_VALUE_KINDS,
  actionBuilderRuleMinimumRisk,
  hasAssignments,
  isCreateRule,
  isLinkRule,
  newActionBuilderAssignment,
  newActionBuilderRule,
  type ActionBuilderDraft,
  type ActionBuilderRule,
  type ActionBuilderValue,
} from "../lib/action-builder-model";

export function ActionBuilderRuleEditor(props: {
  draft: ActionBuilderDraft;
  objects: FoundryLiteOntologyObjectView[];
  links: OntologyCatalogLink[];
  interfaces: OntologyCatalogInterface[];
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const [nextKind, setNextKind] = useState("modifyObject");
  const addRule = () => props.onChange({
    ...props.draft,
    rules: [
      ...props.draft.rules,
      newActionBuilderRule(
        nextKind,
        props.draft.target,
        props.draft.rules.length,
        props.draft.targetKind,
      ),
    ],
  });
  const updateRule = (key: string, next: ActionBuilderRule) => props.onChange({
    ...props.draft,
    rules: props.draft.rules.map((rule) => rule.key === key ? next : rule),
  });
  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">순서 있는 객체·링크 편집 규칙</h2>
          <p className="text-[11px] text-muted-foreground">모든 규칙을 먼저 계획하고 검증한 뒤 하나의 Ontology 트랜잭션으로 커밋합니다.</p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={nextKind} onValueChange={setNextKind}>
            <SelectTrigger className="w-48" aria-label="추가할 규칙 종류"><SelectValue /></SelectTrigger>
            <SelectContent>{ACTION_BUILDER_RULE_KINDS.map((kind) => <SelectItem key={kind} value={kind}>{ruleKindLabel(kind)}</SelectItem>)}</SelectContent>
          </Select>
          <Button size="sm" variant="outline" onClick={addRule}><Plus />규칙</Button>
        </div>
      </div>
      <div className="space-y-3">
        {props.draft.rules.map((rule, index) => (
          <RuleCard
            key={rule.key}
            rule={rule}
            index={index}
            parameters={props.draft.parameters.map((parameter) => parameter.apiName).filter(Boolean)}
            priorRuleIds={props.draft.rules.slice(0, index).map((item) => item.ruleId).filter(Boolean)}
            objects={props.objects}
            links={props.links}
            interfaces={props.interfaces}
            targetKind={props.draft.targetKind}
            target={props.draft.target}
            onChange={(next) => updateRule(rule.key, next)}
            onDelete={() => props.onChange({ ...props.draft, rules: props.draft.rules.filter((item) => item.key !== rule.key) })}
          />
        ))}
      </div>
      {!props.draft.rules.length ? <p className="rounded border border-dashed p-4 text-center text-[11px] text-muted-foreground">Action은 최소 한 개의 편집 규칙이 필요합니다.</p> : null}
    </section>
  );
}

function RuleCard(props: {
  rule: ActionBuilderRule;
  index: number;
  parameters: string[];
  priorRuleIds: string[];
  objects: FoundryLiteOntologyObjectView[];
  links: OntologyCatalogLink[];
  interfaces: OntologyCatalogInterface[];
  targetKind: "object" | "interface";
  target: string;
  onChange: (rule: ActionBuilderRule) => void;
  onDelete: () => void;
}) {
  const update = (values: Partial<ActionBuilderRule>) => props.onChange({ ...props.rule, ...values });
  const changeKind = (kind: string) => {
    const replacement = newActionBuilderRule(
      kind,
      props.target || props.rule.objectType || props.objects[0]?.apiName || "",
      props.index,
      props.targetKind,
    );
    props.onChange({ ...replacement, key: props.rule.key, ruleId: props.rule.ruleId || replacement.ruleId });
  };
  return (
    <div className="space-y-3 rounded border bg-muted/20 p-3">
      <div className="grid items-end gap-2 md:grid-cols-[40px_200px_minmax(0,1fr)_auto]">
        <div className="flex size-8 items-center justify-center rounded border bg-background font-mono text-[11px]">{props.index + 1}</div>
        <Field label="규칙 종류"><Select value={props.rule.kind} onValueChange={changeKind}><SelectTrigger aria-label={`규칙 ${props.index + 1} 종류`}><SelectValue /></SelectTrigger><SelectContent>{ACTION_BUILDER_RULE_KINDS.map((kind) => <SelectItem key={kind} value={kind}>{ruleKindLabel(kind)}</SelectItem>)}</SelectContent></Select></Field>
        <Field label="rule ID"><Input aria-label={`규칙 ${props.index + 1} ID`} value={props.rule.ruleId} onChange={(event) => update({ ruleId: event.target.value })} /></Field>
        <Button size="icon-sm" variant="ghost" aria-label={`규칙 ${props.index + 1} 삭제`} onClick={props.onDelete}><Trash2 /></Button>
      </div>
      <div className="flex items-center justify-between rounded bg-background/70 px-2 py-1.5 text-[10px]">
        <span>{ruleKindDescription(props.rule.kind)}</span>
        <span className="font-mono text-muted-foreground">최소 위험 {actionBuilderRuleMinimumRisk(props.rule.kind)}</span>
      </div>
      {isLinkRule(props.rule.kind) ? (
        <LinkRuleFields {...props} update={update} />
      ) : (
        <ObjectRuleFields {...props} update={update} />
      )}
    </div>
  );
}

function ObjectRuleFields(props: Parameters<typeof RuleCard>[0] & { update: (values: Partial<ActionBuilderRule>) => void }) {
  const object = props.objects.find((item) => item.apiName === props.rule.objectType);
  const interfaceType = props.interfaces.find((item) => item.apiName === props.target);
  const sharedProperties = interfacePropertyOptions(interfaceType);
  const properties = props.targetKind === "interface"
    ? sharedProperties
    : isCreateRule(props.rule.kind) ? object?.properties ?? [] : object?.editableProperties ?? [];
  const addAssignment = () => props.update({
    assignments: [...props.rule.assignments, newActionBuilderAssignment(props.rule.assignments.length)],
  });
  return (
    <div className="space-y-3">
      {props.targetKind === "interface" ? (
        <div className="rounded border bg-background/70 px-3 py-2 text-[11px]">
          <div className="font-medium">Interface 공유 계약 · {interfaceType?.displayName ?? props.target}</div>
          <p className="text-[10px] text-muted-foreground">실행 시 요청의 구체 Object Type을 선택하고 공유 속성만 편집합니다.</p>
        </div>
      ) : (
        <Field label="Object Type"><Select value={props.rule.objectType} onValueChange={(objectType) => props.update({ objectType, assignments: [] })}><SelectTrigger aria-label={`규칙 ${props.index + 1} Object Type`}><SelectValue placeholder="객체 선택" /></SelectTrigger><SelectContent>{props.objects.map((item) => <SelectItem key={item.apiName} value={item.apiName}>{item.displayName} · {item.apiName}</SelectItem>)}</SelectContent></Select></Field>
      )}
      <ValueEditor
        label={isCreateRule(props.rule.kind) ? "생성 객체 primary key" : "편집 대상 객체"}
        value={isCreateRule(props.rule.kind) ? props.rule.primaryKey : props.rule.target}
        parameters={isCreateRule(props.rule.kind) ? props.parameters : [...props.parameters, "__target__"]}
        priorRuleIds={props.priorRuleIds}
        properties={allProperties(props.objects, interfaceType)}
        onChange={(value) => isCreateRule(props.rule.kind) ? props.update({ primaryKey: value }) : props.update({ target: value })}
      />
      {hasAssignments(props.rule.kind) ? (
        <div className="space-y-2 border-t pt-3">
          <div className="flex items-center justify-between"><div><div className="text-[11px] font-medium">속성 편집</div><p className="text-[10px] text-muted-foreground">각 속성 값은 허용된 typed source에서만 가져옵니다.</p></div><Button size="sm" variant="outline" onClick={addAssignment}><Plus />속성</Button></div>
          {props.rule.assignments.map((assignment) => (
            <div key={assignment.key} className="grid items-end gap-2 rounded border bg-background/70 p-2 lg:grid-cols-[220px_minmax(0,1fr)_auto]">
              <Field label="Ontology 속성"><Select value={assignment.property || undefined} onValueChange={(property) => props.update({ assignments: props.rule.assignments.map((item) => item.key === assignment.key ? { ...item, property } : item) })}><SelectTrigger aria-label="매핑 Ontology 속성"><SelectValue placeholder="속성 선택" /></SelectTrigger><SelectContent>{properties.map((property) => <SelectItem key={property.apiName} value={property.apiName}>{property.displayName} · {property.dataType}</SelectItem>)}</SelectContent></Select></Field>
              <ValueEditor label="속성 값" value={assignment.value} parameters={props.parameters} priorRuleIds={props.priorRuleIds} properties={allProperties(props.objects, interfaceType)} onChange={(value) => props.update({ assignments: props.rule.assignments.map((item) => item.key === assignment.key ? { ...item, value } : item) })} />
              <Button size="icon-sm" variant="ghost" aria-label="속성 매핑 삭제" onClick={() => props.update({ assignments: props.rule.assignments.filter((item) => item.key !== assignment.key) })}><Trash2 /></Button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LinkRuleFields(props: Parameters<typeof RuleCard>[0] & { update: (values: Partial<ActionBuilderRule>) => void }) {
  const interfaceType = props.interfaces.find((item) => item.apiName === props.target);
  return (
    <div className="space-y-3">
      {props.targetKind === "interface" ? (
        <Field label="Interface Link Constraint"><Select value={props.rule.interfaceLinkConstraint || undefined} onValueChange={(interfaceLinkConstraint) => props.update({ interfaceLinkConstraint, onInterface: props.target, linkType: "" })}><SelectTrigger aria-label={`규칙 ${props.index + 1} Interface Link Constraint`}><SelectValue placeholder="제약 선택" /></SelectTrigger><SelectContent>{(interfaceType?.linkConstraints ?? []).map((constraint) => <SelectItem key={constraint.apiName} value={constraint.apiName}>{constraint.displayName} · {constraint.targetKind} {constraint.target} · {constraint.cardinality}</SelectItem>)}</SelectContent></Select></Field>
      ) : (
        <Field label="Link Type"><Select value={props.rule.linkType || undefined} onValueChange={(linkType) => props.update({ linkType, onInterface: "", interfaceLinkConstraint: "" })}><SelectTrigger aria-label={`규칙 ${props.index + 1} Link Type`}><SelectValue placeholder="링크 선택" /></SelectTrigger><SelectContent>{props.links.map((link) => <SelectItem key={link.apiName} value={link.apiName}>{link.displayName} · {link.fromObjectType} → {link.toObjectType}</SelectItem>)}</SelectContent></Select></Field>
      )}
      <div className="grid items-center gap-2 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
        <ValueEditor label="링크 시작 객체" value={props.rule.source} parameters={[...props.parameters, "__target__"]} priorRuleIds={props.priorRuleIds} properties={allProperties(props.objects, interfaceType)} onChange={(source) => props.update({ source })} />
        <ArrowRight className="mt-5 size-4 text-muted-foreground" />
        <ValueEditor label="링크 도착 객체" value={props.rule.target} parameters={[...props.parameters, "__target__"]} priorRuleIds={props.priorRuleIds} properties={allProperties(props.objects, interfaceType)} onChange={(target) => props.update({ target })} />
      </div>
    </div>
  );
}

function ValueEditor(props: {
  label: string;
  value: ActionBuilderValue;
  parameters: string[];
  priorRuleIds: string[];
  properties: Array<{ apiName: string; displayName: string }>;
  onChange: (value: ActionBuilderValue) => void;
}) {
  const update = (values: Partial<ActionBuilderValue>) => props.onChange({ ...props.value, ...values });
  const changeKind = (kind: string) => props.onChange({ kind, reference: "", secondary: "", literal: "" });
  return (
    <div className="grid gap-2 sm:grid-cols-[150px_minmax(0,1fr)]">
      <Field label={`${props.label} 출처`}><Select value={props.value.kind} onValueChange={changeKind}><SelectTrigger aria-label={`${props.label} 출처`}><SelectValue /></SelectTrigger><SelectContent>{ACTION_BUILDER_VALUE_KINDS.map((kind) => <SelectItem key={kind} value={kind}>{valueKindLabel(kind)}</SelectItem>)}</SelectContent></Select></Field>
      <Field label={props.label}><ValueReferenceInput {...props} update={update} /></Field>
    </div>
  );
}

function ValueReferenceInput(props: Parameters<typeof ValueEditor>[0] & { update: (values: Partial<ActionBuilderValue>) => void }) {
  if (props.value.kind === "literal") return <Input aria-label={props.label} value={props.value.literal} onChange={(event) => props.update({ literal: event.target.value })} placeholder="문자열 또는 JSON 값" />;
  if (props.value.kind === "parameter") return <OptionSelect label={props.label} value={props.value.reference} options={props.parameters} onChange={(reference) => props.update({ reference })} />;
  if (props.value.kind === "priorRuleOutput") return <div className="grid gap-2 sm:grid-cols-2"><OptionSelect label={`${props.label} 규칙`} value={props.value.reference} options={props.priorRuleIds} onChange={(reference) => props.update({ reference })} /><Input aria-label={`${props.label} 출력`} value={props.value.secondary} onChange={(event) => props.update({ secondary: event.target.value })} placeholder="objectId" /></div>;
  if (props.value.kind === "objectProperty") return <div className="grid gap-2 sm:grid-cols-2"><OptionSelect label={`${props.label} 객체 파라미터`} value={props.value.reference} options={props.parameters.filter((item) => item !== "__target__")} onChange={(reference) => props.update({ reference })} /><OptionSelect label={`${props.label} 객체 속성`} value={props.value.secondary} options={props.properties.map((item) => item.apiName)} onChange={(secondary) => props.update({ secondary })} /></div>;
  if (props.value.kind === "currentUser") return <OptionSelect label={props.label} value={props.value.reference || "id"} options={["id", "groups"]} onChange={(reference) => props.update({ reference })} />;
  if (props.value.kind === "currentTime") return <OptionSelect label={props.label} value={props.value.reference || "timestamp"} options={["timestamp", "date"]} onChange={(reference) => props.update({ reference })} />;
  if (props.value.kind === "generatedId") return <OptionSelect label={props.label} value={props.value.reference || "uuid"} options={["uuid"]} onChange={(reference) => props.update({ reference })} />;
  return <Input aria-label={props.label} value={props.value.reference} onChange={(event) => props.update({ reference: event.target.value })} placeholder="등록된 before-commit 응답 필드" />;
}

function OptionSelect(props: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <Select value={props.value || undefined} onValueChange={props.onChange}><SelectTrigger aria-label={props.label}><SelectValue placeholder="선택" /></SelectTrigger><SelectContent>{props.options.map((option) => <SelectItem key={option} value={option}>{option === "__target__" ? "현재 Action 대상" : option}</SelectItem>)}</SelectContent></Select>;
}

function allProperties(
  objects: FoundryLiteOntologyObjectView[],
  interfaceType?: OntologyCatalogInterface,
): Array<{ apiName: string; displayName: string }> {
  const seen = new Set<string>();
  return [...objects.flatMap((object) => object.properties), ...interfacePropertyOptions(interfaceType)].filter((property) => {
    if (seen.has(property.apiName)) return false;
    seen.add(property.apiName);
    return true;
  });
}

function interfacePropertyOptions(
  interfaceType?: OntologyCatalogInterface,
): Array<{ apiName: string; displayName: string; dataType: string }> {
  return (interfaceType?.properties ?? []).flatMap((property) => {
    const apiName = typeof property.apiName === "string" ? property.apiName : "";
    if (!apiName) return [];
    return [{
      apiName,
      displayName: typeof property.displayName === "string" ? property.displayName : apiName,
      dataType: typeof property.dataType === "string"
        ? property.dataType
        : typeof property.type === "string" ? property.type : "string",
    }];
  });
}

function ruleKindLabel(kind: string): string {
  return ({ modifyObject: "객체 수정", createObject: "객체 생성", createOrModifyObject: "객체 생성 또는 수정", modifyObjects: "객체 집합 수정", deleteObject: "객체 삭제", deleteObjects: "객체 집합 삭제", createLink: "링크 생성", deleteLink: "링크 삭제" } as Record<string, string>)[kind] ?? kind;
}

function ruleKindDescription(kind: string): string {
  if (kind === "createObject") return "새 객체를 만들고 이후 규칙에서 그 ID를 참조할 수 있습니다.";
  if (kind === "createLink" || kind === "deleteLink") return "두 객체 endpoint를 권한 확인 후 연결하거나 해제합니다.";
  if (kind.includes("delete")) return "삭제는 high risk로 자동 상승하고 사람 승인을 요구합니다.";
  if (kind.includes("Objects")) return "Object Set의 모든 대상에 같은 변경을 계획합니다.";
  return "대상 객체의 읽은 버전을 고정해 stale write를 차단합니다.";
}

function valueKindLabel(kind: string): string {
  return ({ parameter: "파라미터", literal: "고정 값", objectProperty: "참조 객체 속성", priorRuleOutput: "앞선 규칙 출력", currentUser: "현재 사용자", currentTime: "현재 시간", generatedId: "생성 ID", webhookResponse: "writeback 응답" } as Record<string, string>)[kind] ?? kind;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1"><Label className="text-[10px]">{label}</Label>{children}</div>;
}
