import type { SourceDebeziumCaptureSemantics } from "@foundry-lite/sdk";
import { Fingerprint, GitCommitHorizontal, ListStart, Trash2 } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

export function CdcCaptureSemanticsCard({
  semantics,
}: {
  semantics: SourceDebeziumCaptureSemantics;
}) {
  const primaryKey = semantics.primaryKey.join(", ") || "미지정";
  return (
    <section className="overflow-hidden rounded border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <div>
          <div className="text-[13px] font-semibold">Capture semantics</div>
          <div className="text-[10px] text-muted-foreground">
            초기 스냅샷 이후 변경 로그를 순서대로 보존하는 계약
          </div>
        </div>
        <StatusPill intent={semantics.primaryKey.length > 0 ? "success" : "warning"}>
          {semantics.primaryKey.length > 0 ? "identity ready" : "primary key 필요"}
        </StatusPill>
      </div>
      <div className="grid divide-y sm:grid-cols-4 sm:divide-x sm:divide-y-0">
        <SemanticMetric
          icon={ListStart}
          label="Snapshot"
          value="initial → changes"
          hint="외부 Debezium connector 책임"
        />
        <SemanticMetric
          icon={GitCommitHorizontal}
          label="Operations"
          value={semantics.operationCodes.join(" / ")}
          hint="read · create · update · delete"
        />
        <SemanticMetric
          icon={Fingerprint}
          label="Primary key"
          value={primaryKey}
          hint="결정적 object identity"
        />
        <SemanticMetric
          icon={Trash2}
          label="Delete"
          value={semantics.deletePolicy}
          hint="topic · partition · offset 순서"
        />
      </div>
    </section>
  );
}

function SemanticMetric({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof ListStart;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <div className="flex min-w-0 gap-2 px-3 py-3">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-primary" />
      <div className="min-w-0">
        <div className="text-[10px] font-semibold tracking-wide text-muted-foreground uppercase">
          {label}
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] font-semibold">
          {value}
        </div>
        <div className="mt-0.5 text-[9px] text-muted-foreground">{hint}</div>
      </div>
    </div>
  );
}
