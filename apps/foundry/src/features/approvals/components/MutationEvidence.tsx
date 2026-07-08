import { CheckCircle2 } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

export type MutationEvidenceData = {
  label: string;
  idempotencyKey: string;
  requestId: string | null;
  extra?: Array<{ key: string; value: string }>;
};

interface MutationEvidenceProps {
  evidence: MutationEvidenceData;
}

/**
 * mutation 성공 후 idempotency key / request id / 결과 식별자를 노출한다.
 * (스펙 구현 원칙: mutation의 멱등성·증거를 숨기지 않는다)
 */
export function MutationEvidence({ evidence }: MutationEvidenceProps) {
  return (
    <div className="space-y-1.5 rounded border border-success/30 bg-success/5 p-2.5">
      <div className="flex items-center gap-1.5 text-[12px] font-medium text-success">
        <CheckCircle2 className="size-3.5" />
        {evidence.label}
        <StatusPill intent="success" className="ml-auto">
          완료
        </StatusPill>
      </div>
      <dl className="space-y-1 font-mono text-[11px] text-muted-foreground">
        <div className="flex justify-between gap-3">
          <dt>idempotency_key</dt>
          <dd className="min-w-0 truncate text-foreground">
            {evidence.idempotencyKey}
          </dd>
        </div>
        {evidence.requestId ? (
          <div className="flex justify-between gap-3">
            <dt>request_id</dt>
            <dd className="min-w-0 truncate text-foreground">
              {evidence.requestId}
            </dd>
          </div>
        ) : null}
        {evidence.extra?.map((entry) => (
          <div key={entry.key} className="flex justify-between gap-3">
            <dt>{entry.key}</dt>
            <dd className="min-w-0 truncate text-foreground">{entry.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
