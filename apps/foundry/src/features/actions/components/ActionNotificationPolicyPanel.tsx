import {
  idempotencyKey,
  type ActionNotificationDeliveryMode,
  type ActionNotificationPolicy,
  type ActionNotificationRecipient,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient, useFoundryLiteMutation } from "@foundry-lite/sdk/react";
import { Plus, Save, ShieldCheck, Trash2, UserRoundCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { useActionNotificationPolicies } from "../use-action-notification-policies";

type RecipientDraft = ActionNotificationRecipient & { key: string };

export function ActionNotificationPolicyPanel(props: ReturnType<typeof useActionNotificationPolicies>) {
  const client = useFoundryLiteClient();
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [draft, setDraft] = useState(() => emptyDraft());
  const selected = props.policies.find((item) => item.policyName === selectedName) ?? null;
  useEffect(() => { if (selected) setDraft(policyDraft(selected)); }, [selected]);
  const save = useFoundryLiteMutation(async () => {
    const recipients = recipientsPayload(draft.recipients);
    if (selected) return client.actions.notificationPolicies.update(selected.policyName, {
      displayName: draft.displayName, deliveryMode: draft.deliveryMode, recipients,
      status: draft.status, expectedFingerprint: selected.configFingerprint,
    }, { idempotencyKey: idempotencyKey("notification-policy-update", selected.policyName) });
    return client.actions.notificationPolicies.create({
      policyName: draft.policyName, displayName: draft.displayName,
      deliveryMode: draft.deliveryMode, recipients,
    }, { idempotencyKey: idempotencyKey("notification-policy-create", draft.policyName) });
  }, { onSuccess: (policy) => {
    setSelectedName(policy.policyName); toast.success("알림 수신자 정책을 저장했습니다"); void props.reload();
  }});
  const disable = useFoundryLiteMutation(async () => {
    if (!selected) throw new Error("Select a notification policy.");
    return client.actions.notificationPolicies.disable(selected.policyName,
      { expectedFingerprint: selected.configFingerprint },
      { idempotencyKey: idempotencyKey("notification-policy-disable", selected.policyName) });
  }, { onSuccess: () => { toast.success("정책을 비활성화했습니다"); void props.reload(); }});

  if (props.isLoading && !props.policies.length) return <LoadingState rowCount={5} />;
  if (props.error && !props.policies.length) return <ErrorState error={props.error} onRetry={() => void props.reload()} />;
  return (
    <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
      <section className="space-y-3 rounded border bg-card p-3">
        <div className="flex items-start justify-between gap-2">
          <div><h2 className="text-sm font-semibold">수신자 정책 원장</h2><p className="text-[10px] text-muted-foreground">Action에는 이름만 저장하고 실제 수신자는 실행 시 다시 판정합니다.</p></div>
          <Button size="sm" variant="outline" aria-label="새 알림 정책" onClick={() => { setSelectedName(null); setDraft(emptyDraft()); }}><Plus />새 정책</Button>
        </div>
        {props.policies.length ? props.policies.map((policy) => (
          <button key={policy.id} type="button" onClick={() => setSelectedName(policy.policyName)} className={`w-full rounded border p-2 text-left ${selectedName === policy.policyName ? "border-primary bg-primary/5" : "bg-background"}`}>
            <div className="flex items-center justify-between gap-2"><span className="text-xs font-medium">{policy.displayName}</span><StatusPill intent={policy.status === "active" ? "success" : "neutral"}>{policy.status}</StatusPill></div>
            <div className="mt-1 font-mono text-[9px] text-muted-foreground">{policy.targetRef} · v{policy.version} · {policy.recipients.length}명</div>
          </button>
        )) : <EmptyState title="등록된 정책이 없습니다" description="첫 수신자 정책을 만들면 Action Builder에서 선택할 수 있습니다." />}
      </section>
      <section className="space-y-4 rounded border bg-card p-4">
        <div className="flex items-start justify-between gap-3">
          <div><h2 className="text-sm font-semibold">{selected ? "알림 정책 편집" : "알림 정책 만들기"}</h2><p className="text-[10px] text-muted-foreground">수신자마다 현재 객체 read 권한과 행 가시성을 통과해야 전달됩니다.</p></div>
          <ShieldCheck className="size-5 text-primary" />
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Policy API name"><Input aria-label="알림 정책 API name" value={draft.policyName} disabled={Boolean(selected)} onChange={(event) => setDraft({ ...draft, policyName: event.target.value })} placeholder="operations" /></Field>
          <Field label="표시 이름"><Input aria-label="알림 정책 표시 이름" value={draft.displayName} onChange={(event) => setDraft({ ...draft, displayName: event.target.value })} /></Field>
          <Field label="전달 정책"><Select value={draft.deliveryMode} onValueChange={(value) => setDraft({ ...draft, deliveryMode: value as ActionNotificationDeliveryMode })}><SelectTrigger aria-label="알림 정책 전달 방식"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="strict">Strict · 한 명이라도 거부되면 전체 중단</SelectItem><SelectItem value="best_effort">Best effort · 허용된 수신자만 전달</SelectItem></SelectContent></Select></Field>
          {selected ? <Field label="상태"><Select value={draft.status} onValueChange={(value) => setDraft({ ...draft, status: value as "active" | "disabled" })}><SelectTrigger aria-label="알림 정책 상태"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="active">active</SelectItem><SelectItem value="disabled">disabled</SelectItem></SelectContent></Select></Field> : null}
        </div>
        <div className="space-y-2 border-t pt-3">
          <div className="flex items-center justify-between"><div><div className="text-[11px] font-medium">수신자와 현재 역할</div><p className="text-[10px] text-muted-foreground">쉼표로 역할을 입력합니다. 이 역할만으로 전달되지 않고 객체별 권한도 다시 확인합니다.</p></div><Button size="sm" variant="outline" onClick={() => setDraft({ ...draft, recipients: [...draft.recipients, newRecipient(draft.recipients.length)] })}><Plus />수신자</Button></div>
          {draft.recipients.map((recipient, index) => (
            <div key={recipient.key} className="grid gap-2 rounded border bg-muted/20 p-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <Input aria-label={`알림 수신자 ${index + 1} user ID`} value={recipient.userId} onChange={(event) => updateRecipient(draft, setDraft, recipient.key, { userId: event.target.value })} placeholder="user-id" />
              <Input aria-label={`알림 수신자 ${index + 1} roles`} value={recipient.roles.join(", ")} onChange={(event) => updateRecipient(draft, setDraft, recipient.key, { roles: event.target.value.split(",") })} placeholder="ops_manager, admin" />
              <Button size="icon-sm" variant="ghost" aria-label={`알림 수신자 ${index + 1} 삭제`} disabled={draft.recipients.length === 1} onClick={() => setDraft({ ...draft, recipients: draft.recipients.filter((item) => item.key !== recipient.key) })}><Trash2 /></Button>
            </div>
          ))}
        </div>
        <div className="rounded border bg-muted/20 p-3 text-[10px] text-muted-foreground"><UserRoundCheck className="mr-2 inline size-4 text-primary" />정책 변경은 fingerprint CAS·멱등 키·감사 로그·outbox를 한 트랜잭션으로 기록합니다.</div>
        {save.error ? <ErrorState error={save.error} /> : null}
        {disable.error ? <ErrorState error={disable.error} /> : null}
        <div className="flex justify-end gap-2">
          {selected && selected.status === "active" ? <Button variant="destructive" disabled={disable.isRunning} onClick={() => void disable.execute(undefined)}>비활성화</Button> : null}
          <Button disabled={save.isRunning || !isDraftValid(draft)} onClick={() => void save.execute(undefined)}><Save />{selected ? "정책 갱신" : "정책 생성"}</Button>
        </div>
      </section>
    </div>
  );
}

type PolicyDraft = { policyName: string; displayName: string; deliveryMode: ActionNotificationDeliveryMode; status: "active" | "disabled"; recipients: RecipientDraft[] };
function emptyDraft(): PolicyDraft { return { policyName: "", displayName: "", deliveryMode: "strict", status: "active", recipients: [newRecipient(0)] }; }
function policyDraft(policy: ActionNotificationPolicy): PolicyDraft { return { policyName: policy.policyName, displayName: policy.displayName, deliveryMode: policy.deliveryMode, status: policy.status, recipients: policy.recipients.map((item, index) => ({ ...item, key: `recipient-${index}-${item.userId}` })) }; }
function newRecipient(index: number): RecipientDraft { return { key: `recipient-${Date.now()}-${index}`, userId: "", roles: [""] }; }
function recipientsPayload(items: RecipientDraft[]): ActionNotificationRecipient[] { return items.map((item) => ({ userId: item.userId.trim(), roles: item.roles.map((role) => role.trim()).filter(Boolean) })); }
function isDraftValid(draft: PolicyDraft): boolean { return Boolean(draft.policyName.trim() && draft.displayName.trim() && recipientsPayload(draft.recipients).every((item) => item.userId && item.roles.length)); }
function updateRecipient(draft: PolicyDraft, setDraft: (value: PolicyDraft) => void, key: string, values: Partial<RecipientDraft>) { setDraft({ ...draft, recipients: draft.recipients.map((item) => item.key === key ? { ...item, ...values } : item) }); }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <div className="space-y-1"><Label className="text-[10px]">{label}</Label>{children}</div>; }
