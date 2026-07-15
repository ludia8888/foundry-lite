import type { SourceConnectionTestResult } from "@foundry-lite/sdk";
import { AlertTriangle, History } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { formatTimestamp, statusIntent, statusLabel } from "../source-model";
import { SettingsCard } from "./SourceSettingsUi";

interface SourceConnectionTestHistoryProps {
  tests: readonly SourceConnectionTestResult[];
  currentConfigFingerprint: string;
  selectedTestId: string | null;
  onSelect: (test: SourceConnectionTestResult) => void;
}

/** Persisted diagnostic evidence, newest first. */
export function SourceConnectionTestHistory({
  tests,
  currentConfigFingerprint,
  selectedTestId,
  onSelect,
}: SourceConnectionTestHistoryProps) {
  return (
    <SettingsCard
      title="진단 이력"
      description="새로고침 뒤에도 남는 Source 연결 검사와 요청 ID입니다."
      actions={<span className="text-[10px] text-muted-foreground">최근 {tests.length}건</span>}
    >
      {tests.length === 0 ? (
        <div className="flex items-center gap-2 px-4 py-5 text-xs text-muted-foreground">
          <History aria-hidden className="size-4" /> 아직 저장된 연결 진단이 없습니다.
        </div>
      ) : (
        <ol className="divide-y divide-border/60">
          {tests.map((test) => {
            const isStale = test.configFingerprint !== currentConfigFingerprint;
            return (
              <li key={test.connectionTestId}>
                <Button
                  type="button"
                  variant="ghost"
                  className={cn(
                    "h-auto w-full justify-start rounded-none px-4 py-3 text-left",
                    selectedTestId === test.connectionTestId && "bg-muted/50",
                  )}
                  aria-pressed={selectedTestId === test.connectionTestId}
                  onClick={() => onSelect(test)}
                >
                  <span className="flex min-w-0 flex-1 items-start justify-between gap-3">
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <StatusPill intent={statusIntent(test.status)}>
                          {statusLabel(test.status)}
                        </StatusPill>
                        <span className="text-[11px] font-medium">
                          {formatTimestamp(test.completedAt ?? test.startedAt)}
                        </span>
                        {isStale ? (
                          <span className="inline-flex items-center gap-1 text-[10px] text-warning">
                            <AlertTriangle aria-hidden className="size-3" /> 이전 설정
                          </span>
                        ) : null}
                      </span>
                      <span className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">
                        request {test.requestId ?? "—"}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                      {test.checks.summary.passed}/{test.checks.summary.total} checks
                    </span>
                  </span>
                </Button>
              </li>
            );
          })}
        </ol>
      )}
    </SettingsCard>
  );
}
