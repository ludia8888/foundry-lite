import { ArrowRight, ShieldCheck } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

import type { ActionDefinitionView } from "../lib/action-definition";

interface SubmissionCriteriaPanelProps {
  definition: ActionDefinitionView;
}

/** 제출 기준 / 규칙 패널: 권한·전제조건·변경·writeback·side effect를 카탈로그 메타에서 그대로 노출. */
export function SubmissionCriteriaPanel({
  definition,
}: SubmissionCriteriaPanelProps) {
  return (
    <div className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-center gap-1.5">
        <ShieldCheck className="size-3.5 text-muted-foreground" />
        <span className="section-label">제출 기준 · 규칙</span>
      </div>

      <div className="grid gap-2 sm:grid-cols-3">
        {[
          ["조회", definition.viewRoles],
          ["정의 편집", definition.editRoles],
          ["실행", definition.applyRoles],
        ].map(([label, roles]) => (
          <div key={label as string} className="space-y-1 rounded border bg-muted/20 p-2">
            <div className="text-[10px] font-medium text-muted-foreground">{label as string} 역할</div>
            {(roles as string[]).length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {(roles as string[]).map((role) => <StatusPill key={role} intent="info">{role}</StatusPill>)}
              </div>
            ) : <div className="text-[10px] text-muted-foreground">기존 전역 정책</div>}
          </div>
        ))}
      </div>

      <div className="space-y-1">
        <div className="text-[11px] font-medium text-muted-foreground">
          전제조건 (precondition)
        </div>
        {definition.preconditions.length > 0 ? (
          <ul className="space-y-1">
            {definition.preconditions.map((precondition, index) => (
              <li
                key={index}
                className="rounded border bg-muted/40 px-2 py-1 text-[11px]"
              >
                <div className="text-foreground/90">{precondition.message}</div>
                {precondition.expression ? (
                  <code className="font-mono text-[10px] text-muted-foreground">
                    {precondition.expression}
                  </code>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[11px] text-muted-foreground">없음</div>
        )}
      </div>

      <div className="space-y-1">
        <div className="text-[11px] font-medium text-muted-foreground">
          적용 변경 (mutation)
        </div>
        {definition.mutations.length > 0 ? (
          <ul className="space-y-0.5 font-mono text-[10px] text-muted-foreground">
            {definition.mutations.map((mutation, index) => (
              <li key={index} className="flex items-center gap-1">
                <span className="text-foreground/80">
                  {mutation.property ?? mutation.type}
                </span>
                <ArrowRight className="size-2.5" />
                <span>{mutation.value ?? "—"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-[11px] text-muted-foreground">없음</div>
        )}
      </div>

      {definition.hasWritebacks ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-muted-foreground">
            외부 writeback
          </div>
          <ul className="space-y-0.5">
            {definition.writebacks.map((writeback, index) => (
              <li
                key={index}
                className="flex flex-wrap items-center gap-1.5 text-[11px]"
              >
                <StatusPill intent="warning">{writeback.apiName}</StatusPill>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {writeback.mode ?? "—"} · {writeback.connector ?? "—"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {definition.sideEffects.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-muted-foreground">
            side effect
          </div>
          <ul className="space-y-0.5 font-mono text-[10px] text-muted-foreground">
            {definition.sideEffects.map((sideEffect, index) => (
              <li key={index}>
                {sideEffect.type}
                {sideEffect.topic ? ` · ${sideEffect.topic}` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
