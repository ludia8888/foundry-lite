import { Link } from "react-router";

import { StatusPill } from "@/components/shared/StatusPill";
import { operationsRunHref } from "@/lib/operations-links";

export type ActionRunEvidenceData = {
  status?: string;
  actionRunId?: string;
  newObjectVersion?: number;
  objectEditId?: string;
  idempotentReplay?: boolean;
};

interface ActionRunEvidenceProps {
  result: ActionRunEvidenceData;
  requestId: string | null;
}

/** apply 성공 evidence: run_id/edit_id/new_version/request_id + idempotent replay 배지. */
export function ActionRunEvidence({
  result,
  requestId,
}: ActionRunEvidenceProps) {
  return (
    <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill intent="success">{result.status ?? "적용됨"}</StatusPill>
        {result.idempotentReplay ? (
          <StatusPill intent="info">idempotent replay</StatusPill>
        ) : null}
      </div>
      <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
        {result.actionRunId ? (
          <div>
            run_id=
            <Link
              to={operationsRunHref(result.actionRunId, "action")}
              className="text-primary hover:underline"
            >
              {result.actionRunId}
            </Link>
          </div>
        ) : null}
        {result.objectEditId ? <div>edit_id={result.objectEditId}</div> : null}
        {result.newObjectVersion !== undefined ? (
          <div>new_version=v{result.newObjectVersion}</div>
        ) : null}
        {requestId ? <div>request_id={requestId}</div> : null}
      </div>
    </div>
  );
}
