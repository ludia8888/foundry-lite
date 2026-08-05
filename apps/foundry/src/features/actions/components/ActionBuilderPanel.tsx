import {
  idempotencyKey,
  normalizeFoundryLiteError,
  type FoundryLiteApiError,
  type OntologyCatalogFunction,
  type OntologyCatalogInterface,
  type OntologyCatalogLink,
  type OntologyBranchActionType,
  type ActionNotificationPolicy,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteProvidedOntologyBranch,
  useFoundryLiteProvidedOntologyBranches,
  useFoundryLiteProvidedOntologyProposal,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import {
  ArrowRight,
  CheckCircle2,
  GitBranch,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
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
import { Textarea } from "@/components/ui/textarea";

import {
  actionBuilderDefinition,
  actionBuilderDraftFromItem,
  actionBuilderValidationMessage,
  emptyActionBuilderDraft,
  interfaceProperties,
  linkedCriteriaProperties,
  newActionBuilderRule,
  objectProperties,
  type ActionBuilderDraft,
} from "../lib/action-builder-model";
import {
  ActionBuilderSubmissionCriteriaEditor,
} from "./ActionBuilderPolicyEditors";
import { ActionBuilderParameterEditor } from "./ActionBuilderParameterEditor";
import { ActionBuilderRuleEditor } from "./ActionBuilderRuleEditor";
import { ActionBuilderExecutionEffectsEditor } from "./ActionBuilderExecutionEffectsEditor";

interface ActionBuilderPanelProps {
  objects: FoundryLiteOntologyObjectView[];
  links: OntologyCatalogLink[];
  interfaces: OntologyCatalogInterface[];
  functions: OntologyCatalogFunction[];
  notificationPolicies: ActionNotificationPolicy[];
}

export function ActionBuilderPanel({ objects, links, interfaces, functions, notificationPolicies }: ActionBuilderPanelProps) {
  const client = useFoundryLiteClient();
  const navigate = useNavigate();
  const branches = useFoundryLiteProvidedOntologyBranches({ status: "open", pageSize: 100 });
  const [branchId, setBranchId] = useState<string | null>(null);
  const branch = useFoundryLiteProvidedOntologyBranch(branchId);
  const proposal = useFoundryLiteProvidedOntologyProposal(branch.branch?.proposalId);
  const [items, setItems] = useState<OntologyBranchActionType[]>([]);
  const [selectedApiName, setSelectedApiName] = useState<string | null>(null);
  const [branchFingerprint, setBranchFingerprint] = useState<string | null>(null);
  const [persistedDraftSignature, setPersistedDraftSignature] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<FoundryLiteApiError | null>(null);
  const [draft, setDraft] = useState<ActionBuilderDraft>(() =>
    emptyActionBuilderDraft(objects[0]?.apiName),
  );
  const activeBranchRef = useRef(branchId);
  activeBranchRef.current = branchId;

  useEffect(() => {
    if (!branchId && branches.branches[0]) setBranchId(branches.branches[0].id);
  }, [branchId, branches.branches]);

  const loadItems = useCallback(async () => {
    if (!branchId) return;
    try {
      const result = await client.ontology.branches.actionTypes.list(branchId);
      if (activeBranchRef.current !== branchId) return;
      setItems(result.items);
      setBranchFingerprint(result.branchFingerprint);
      setLoadError(null);
    } catch (error) {
      setLoadError(normalizeFoundryLiteError(error));
    }
  }, [branchId, client]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const selectedItem = useMemo(
    () => items.find((item) => item.apiName === selectedApiName) ?? null,
    [items, selectedApiName],
  );
  const properties = useMemo(
    () => draft.targetKind === "interface"
      ? interfaceProperties(interfaces, draft.target)
      : objectProperties(objects, draft.target),
    [draft.target, draft.targetKind, interfaces, objects],
  );
  const linkedProperties = useMemo(
    () => linkedCriteriaProperties(objects, links, draft.target, draft.targetKind),
    [draft.target, draft.targetKind, links, objects],
  );
  const validationMessage = actionBuilderValidationMessage(draft);
  const draftSignature = useMemo(
    () => JSON.stringify(actionBuilderDefinition(draft)),
    [draft],
  );
  const isDraftPersisted = useMemo(
    () => selectedItem !== null
      && persistedDraftSignature === draftSignature,
    [draftSignature, persistedDraftSignature, selectedItem],
  );

  const saveMutation = useFoundryLiteMutation(async () => {
    if (!branchId || !branchFingerprint) throw new Error("Select an open Ontology branch.");
    const payload = {
      definition: actionBuilderDefinition(draft),
      expectedFingerprint: branchFingerprint,
    };
    const key = idempotencyKey("ontology-branch-action", `${branchId}:${draft.apiName}`);
    return selectedItem
      ? client.ontology.branches.actionTypes.update(branchId, selectedItem.apiName, payload, {
          idempotencyKey: key,
        })
      : client.ontology.branches.actionTypes.create(branchId, payload, { idempotencyKey: key });
  }, {
    onSuccess: (result) => {
      setBranchFingerprint(result.branch.contentFingerprint);
      setPersistedDraftSignature(draftSignature);
      setSelectedApiName(result.actionType?.apiName ?? null);
      toast.success(selectedItem ? "Action 정의를 갱신했습니다" : "Action 정의를 브랜치에 만들었습니다");
      void loadItems();
      void branch.refetch();
    },
  });

  const deleteMutation = useFoundryLiteMutation(async () => {
    if (!branchId || !branchFingerprint || !selectedItem) throw new Error("Select an Action Type.");
    return client.ontology.branches.actionTypes.delete(
      branchId,
      selectedItem.apiName,
      { expectedFingerprint: branchFingerprint },
      {
        idempotencyKey: idempotencyKey(
          "ontology-branch-action-delete",
          `${branchId}:${selectedItem.apiName}`,
        ),
      },
    );
  }, {
    onSuccess: (result) => {
      setBranchFingerprint(result.branch.contentFingerprint);
      setSelectedApiName(null);
      setPersistedDraftSignature(null);
      setDraft(emptyActionBuilderDraft(objects[0]?.apiName));
      toast.success("Action 정의를 브랜치에서 삭제했습니다");
      void loadItems();
      void branch.refetch();
    },
  });

  const proposeMutation = useFoundryLiteMutation(async () => {
    if (!branchId || !branch.branch) throw new Error("Select an open Ontology branch.");
    if (!isDraftPersisted) throw new Error("Save and validate the Action contract before proposing it.");
    return client.ontology.branches.propose(
      branchId,
      {
        title: `${draft.displayName || draft.apiName} Action 활성화`,
        description: `${draft.apiName} ActionDefinitionV3 계약을 검토하고 active Ontology에 반영합니다.`,
      },
      { idempotencyKey: idempotencyKey("ontology.branches.propose", branchId) },
    );
  }, {
    onSuccess: (result) => {
      toast.success("Ontology 변경 제안을 만들었습니다. 승인 큐에서 검토하세요.");
      void branch.refetch();
      void branches.reload();
      const proposalId = result.proposalId ?? result.proposal?.id;
      if (proposalId) navigate(`/approvals?source=ontology&proposalId=${encodeURIComponent(proposalId)}`);
    },
  });

  const selectItem = (item: OntologyBranchActionType) => {
    const nextDraft = actionBuilderDraftFromItem(item);
    setSelectedApiName(item.apiName);
    setDraft(nextDraft);
    setPersistedDraftSignature(JSON.stringify(actionBuilderDefinition(nextDraft)));
  };
  const startNew = () => {
    setSelectedApiName(null);
    setDraft(emptyActionBuilderDraft(objects[0]?.apiName));
    setPersistedDraftSignature(null);
  };

  if (!branches.isLoading && branches.branches.length === 0) {
    return (
      <EmptyState
        title="열린 Ontology 브랜치가 필요합니다"
        description="Action Builder는 active Ontology를 직접 바꾸지 않습니다. Ontology Manager에서 브랜치를 먼저 만드세요."
      />
    );
  }

  return (
    <div className="space-y-3">
      <ContractRail
        isSaved={isDraftPersisted}
        proposalStatus={proposal.proposal?.status ?? null}
        isActivated={Boolean(branch.branch?.mergedVersionNumber || proposal.proposal?.executionStatus === "executed")}
      />
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[250px_minmax(0,1fr)_300px]">
        <BuilderCatalog
          branches={branches.branches}
          branchId={branchId}
          items={items}
          selectedApiName={selectedApiName}
          onBranchChange={(value) => {
            setBranchId(value);
            startNew();
          }}
          onNew={startNew}
          onSelect={selectItem}
        />
        <div className="space-y-3">
          <MetadataEditor
            draft={draft}
            objects={objects}
            interfaces={interfaces}
            isExisting={selectedItem !== null}
            onChange={setDraft}
          />
          <ActionBuilderParameterEditor draft={draft} properties={properties} onChange={setDraft} />
          <ActionBuilderSubmissionCriteriaEditor
            draft={draft}
            properties={properties}
            linkedProperties={linkedProperties}
            onChange={setDraft}
          />
          <ActionBuilderExecutionEffectsEditor
            draft={draft}
            functions={functions}
            notificationPolicies={notificationPolicies}
            actionTypes={items}
            onChange={setDraft}
          />
          {draft.executionMode === "rules" ? (
            <ActionBuilderRuleEditor
              draft={draft}
              objects={objects}
              links={links}
              interfaces={interfaces}
              onChange={setDraft}
            />
          ) : null}
          {loadError ? <ErrorState error={loadError} onRetry={loadItems} /> : null}
          {saveMutation.error ? <ErrorState error={saveMutation.error} /> : null}
          {deleteMutation.error ? <ErrorState error={deleteMutation.error} /> : null}
          {proposeMutation.error ? <ErrorState error={proposeMutation.error} /> : null}
        </div>
        <BuilderEvidence
          draft={draft}
          branchName={branch.branch?.name ?? null}
          fingerprint={branchFingerprint}
          validationMessage={validationMessage}
          isExisting={selectedItem !== null}
          isSaving={saveMutation.isRunning}
          isDeleting={deleteMutation.isRunning}
          isProposing={proposeMutation.isRunning}
          isDraftPersisted={isDraftPersisted}
          isBaseStale={branch.baseStale}
          proposalId={branch.branch?.proposalId ?? null}
          proposalStatus={proposal.proposal?.status ?? null}
          executionStatus={proposal.proposal?.executionStatus ?? null}
          onSave={() => void saveMutation.execute(undefined)}
          onDelete={() => void deleteMutation.execute(undefined)}
          onPropose={() => void proposeMutation.execute(undefined)}
          onOpenApprovals={() => navigate(`/approvals?source=ontology${branch.branch?.proposalId ? `&proposalId=${encodeURIComponent(branch.branch.proposalId)}` : ""}`)}
        />
      </div>
    </div>
  );
}

function ContractRail({ isSaved, proposalStatus, isActivated }: { isSaved: boolean; proposalStatus: string | null; isActivated: boolean }) {
  const isApproved = proposalStatus === "approved" || proposalStatus === "applied" || isActivated;
  const steps = [
    { label: "계약 정의", isReady: isSaved },
    { label: "브랜치 검증", isReady: isSaved },
    { label: proposalStatus ? `제안 · ${proposalStatus}` : "제안 · 승인", isReady: isApproved },
    { label: "활성 실행", isReady: isActivated },
  ];
  return (
    <div className="flex flex-wrap items-center gap-1 rounded border bg-card px-3 py-2" aria-label="Action lifecycle">
      {steps.map((step, index) => (
        <div key={step.label} className="flex items-center gap-1">
          <div className="flex items-center gap-1.5 rounded px-2 py-1 text-[11px]">
            {step.isReady ? <CheckCircle2 className="size-3.5 text-success" /> : <span className="size-2 rounded-full bg-border" />}
            <span className={step.isReady ? "font-medium" : "text-muted-foreground"}>{step.label}</span>
          </div>
          {index < steps.length - 1 ? <ArrowRight className="size-3 text-muted-foreground" /> : null}
        </div>
      ))}
      <span className="ml-auto text-[10px] text-muted-foreground">active Ontology 직접 편집 금지</span>
    </div>
  );
}

type BuilderCatalogProps = {
  branches: Array<{ id: string; name: string }>;
  branchId: string | null;
  items: OntologyBranchActionType[];
  selectedApiName: string | null;
  onBranchChange: (branchId: string) => void;
  onNew: () => void;
  onSelect: (item: OntologyBranchActionType) => void;
};

function BuilderCatalog(props: BuilderCatalogProps) {
  return (
    <aside className="space-y-3 rounded border bg-card p-3">
      <div className="space-y-1">
        <Label className="text-[11px]">작업 브랜치</Label>
        <Select value={props.branchId ?? undefined} onValueChange={props.onBranchChange}>
          <SelectTrigger size="sm" className="w-full text-xs" aria-label="작업 브랜치"><GitBranch className="size-3.5" /><SelectValue /></SelectTrigger>
          <SelectContent>{props.branches.map((branch) => <SelectItem key={branch.id} value={branch.id}>{branch.name}</SelectItem>)}</SelectContent>
        </Select>
      </div>
      <div className="flex items-center justify-between border-t pt-3">
        <span className="section-label">Action Types</span>
        <Button size="sm" variant="ghost" className="h-7 px-2" onClick={props.onNew}><Plus />새 Action</Button>
      </div>
      <div className="space-y-1">
        {props.items.map((item) => (
          <button
            key={item.apiName}
            type="button"
            className={`w-full rounded border px-2 py-2 text-left ${props.selectedApiName === item.apiName ? "border-primary bg-primary/5" : "border-transparent hover:bg-muted"}`}
            onClick={() => props.onSelect(item)}
          >
            <div className="truncate text-xs font-medium">{item.displayName}</div>
            <div className="truncate font-mono text-[10px] text-muted-foreground">{item.apiName}</div>
          </button>
        ))}
        {props.items.length === 0 ? <p className="py-4 text-center text-[11px] text-muted-foreground">이 브랜치에는 Action이 없습니다.</p> : null}
      </div>
    </aside>
  );
}

function MetadataEditor({ draft, objects, interfaces, isExisting, onChange }: { draft: ActionBuilderDraft; objects: FoundryLiteOntologyObjectView[]; interfaces: OntologyCatalogInterface[]; isExisting: boolean; onChange: (draft: ActionBuilderDraft) => void }) {
  const update = (values: Partial<ActionBuilderDraft>) => onChange({ ...draft, ...values });
  const changeTargetKind = (targetKind: ActionBuilderDraft["targetKind"]) => {
    const target = targetKind === "interface"
      ? interfaces[0]?.apiName ?? ""
      : objects[0]?.apiName ?? "";
    onChange(retargetDraft(draft, targetKind, target));
  };
  const changeTarget = (target: string) => onChange(retargetDraft(draft, draft.targetKind, target));
  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div><h2 className="text-sm font-semibold">계약과 정책</h2><p className="text-[11px] text-muted-foreground">사람·앱·AI가 함께 사용할 단일 Action 계약입니다.</p></div>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="API name"><Input aria-label="Action API name" value={draft.apiName} disabled={isExisting} onChange={(event) => update({ apiName: event.target.value })} placeholder="SetOrderStatus" /></Field>
        <Field label="표시 이름"><Input aria-label="Action 표시 이름" value={draft.displayName} onChange={(event) => update({ displayName: event.target.value })} placeholder="주문 상태 변경" /></Field>
        <Field label="대상 계약"><Select value={draft.targetKind} onValueChange={(value) => changeTargetKind(value as ActionBuilderDraft["targetKind"])}><SelectTrigger aria-label="Action 대상 종류"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="object">Object Type</SelectItem><SelectItem value="interface">Interface</SelectItem></SelectContent></Select></Field>
        <Field label={draft.targetKind === "interface" ? "대상 Interface" : "대상 객체"}><Select value={draft.target || undefined} onValueChange={changeTarget}><SelectTrigger aria-label="Action 대상 객체"><SelectValue placeholder={draft.targetKind === "interface" ? "Interface 선택" : "객체 선택"} /></SelectTrigger><SelectContent>{(draft.targetKind === "interface" ? interfaces : objects).map((item) => <SelectItem key={item.apiName} value={item.apiName}>{item.displayName} · {item.apiName}</SelectItem>)}</SelectContent></Select></Field>
        <Field label="조회 역할"><Input aria-label="Action 조회 역할" value={draft.viewRoles} onChange={(event) => update({ viewRoles: event.target.value })} placeholder="viewer, ops_manager" /></Field>
        <Field label="정의 편집 역할"><Input aria-label="Action 편집 역할" value={draft.editRoles} onChange={(event) => update({ editRoles: event.target.value })} placeholder="data_engineer" /></Field>
        <Field label="실행 역할"><Input aria-label="Action 실행 역할" value={draft.applyRoles} onChange={(event) => update({ applyRoles: event.target.value })} placeholder="ops_manager" /></Field>
        <Field label="위험 등급"><Select value={draft.riskLevel} onValueChange={(value) => update({ riskLevel: value as ActionBuilderDraft["riskLevel"] })}><SelectTrigger aria-label="Action 위험 등급"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="low">Low</SelectItem><SelectItem value="medium">Medium</SelectItem><SelectItem value="high">High</SelectItem></SelectContent></Select></Field>
        <Field label="AI 실행 정책"><Select value={draft.agentExecutionPolicy} onValueChange={(value) => update({ agentExecutionPolicy: value as ActionBuilderDraft["agentExecutionPolicy"] })}><SelectTrigger aria-label="Action AI 실행 정책"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="plan_only">계획만</SelectItem><SelectItem value="approval_required">승인 필요</SelectItem><SelectItem value="autonomous">저위험 자동 실행 허용</SelectItem></SelectContent></Select></Field>
      </div>
      <Field label="설명"><Textarea aria-label="Action 설명" rows={2} value={draft.description} onChange={(event) => update({ description: event.target.value })} /></Field>
      <Field label="AI tool 설명"><Textarea aria-label="Action AI tool 설명" rows={2} value={draft.agentToolDescription} onChange={(event) => update({ agentToolDescription: event.target.value })} placeholder="언제 이 Action을 선택하고 무엇을 변경하는지 명확히 설명합니다." /></Field>
    </section>
  );
}

function retargetDraft(
  draft: ActionBuilderDraft,
  targetKind: ActionBuilderDraft["targetKind"],
  target: string,
): ActionBuilderDraft {
  const rules = draft.rules.map((rule, index) => {
    const replacement = newActionBuilderRule(rule.kind, target, index, targetKind);
    return {
      ...replacement,
      key: rule.key,
      ruleId: rule.ruleId || replacement.ruleId,
      source: rule.source,
      target: rule.target,
    };
  });
  return {
    ...draft,
    targetKind,
    target,
    rules,
    executionMode: targetKind === "interface" ? "rules" : draft.executionMode,
    functionApiName: targetKind === "interface" ? "" : draft.functionApiName,
    functionVersion: targetKind === "interface" ? "" : draft.functionVersion,
  };
}

type BuilderEvidenceProps = { draft: ActionBuilderDraft; branchName: string | null; fingerprint: string | null; validationMessage: string | null; isExisting: boolean; isSaving: boolean; isDeleting: boolean; isProposing: boolean; isDraftPersisted: boolean; isBaseStale: boolean; proposalId: string | null; proposalStatus: string | null; executionStatus: string | null; onSave: () => void; onDelete: () => void; onPropose: () => void; onOpenApprovals: () => void };
function BuilderEvidence(props: BuilderEvidenceProps) {
  return (
    <aside className="sticky top-0 space-y-3 rounded border bg-card p-3">
      <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><h2 className="text-sm font-semibold">계약 증거</h2></div>
      <dl className="space-y-2 text-[11px]"><Evidence label="브랜치" value={props.branchName ?? "선택 필요"} /><Evidence label="계약 버전" value="ActionDefinitionV3" /><Evidence label="대상" value={props.draft.target || "미지정"} /><Evidence label="위험" value={props.draft.riskLevel} /><Evidence label="AI 정책" value={props.draft.agentExecutionPolicy} /><Evidence label="브랜치 CAS" value={props.fingerprint ? `${props.fingerprint.slice(0, 22)}…` : "대기"} mono /><Evidence label="제안" value={props.proposalStatus ?? "아직 없음"} /><Evidence label="활성화" value={props.executionStatus ?? "아직 없음"} /></dl>
      <div className="rounded bg-muted/50 p-2"><div className="mb-1 flex items-center justify-between"><span className="text-[11px] font-medium">서버 검증</span><StatusPill intent={props.validationMessage ? "warning" : "success"}>{props.validationMessage ? "입력 필요" : "준비"}</StatusPill></div><p className="text-[10px] text-muted-foreground">{props.validationMessage ?? "저장 시 전체 Ontology 참조·타입·위험도·폼 계약을 원자적으로 검증합니다."}</p></div>
      <Button className="w-full" disabled={Boolean(props.validationMessage) || props.isSaving} onClick={props.onSave}><Save />{props.isSaving ? "검증·저장 중…" : props.isExisting ? "변경 검증·저장" : "브랜치에 생성"}</Button>
      {props.isExisting ? <Button className="w-full" variant="outline" disabled={props.isDeleting} onClick={props.onDelete}><Trash2 />{props.isDeleting ? "삭제 중…" : "브랜치에서 삭제"}</Button> : null}
      {!props.proposalId ? <Button className="w-full" variant="outline" disabled={!props.isDraftPersisted || props.isBaseStale || props.isProposing} onClick={props.onPropose}><GitBranch />{props.isProposing ? "제안 생성 중…" : props.isBaseStale ? "리베이스 후 제안 가능" : "변경 제안 만들기"}</Button> : <Button className="w-full" variant="outline" onClick={props.onOpenApprovals}><ShieldCheck />승인 큐에서 검토</Button>}
      <p className="text-[10px] leading-relaxed text-muted-foreground">저장만으로 production에 활성화되지 않습니다. 브랜치 diff와 제안 리뷰를 통과해야 합니다.</p>
    </aside>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <div className="space-y-1"><Label className="text-[11px]">{label}</Label>{children}</div>; }
function Evidence({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="flex items-start justify-between gap-3"><dt className="text-muted-foreground">{label}</dt><dd className={`max-w-44 break-all text-right ${mono ? "font-mono text-[10px]" : ""}`}>{value}</dd></div>; }
