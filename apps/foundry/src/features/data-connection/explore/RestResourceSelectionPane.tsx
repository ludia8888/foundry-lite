import type {
  ConnectorResource,
  ConnectorResourceTestResult,
} from "@foundry-lite/sdk";
import { ChevronRight, KeyRound, Table2 } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

interface RestResourceSelectionPaneProps {
  resource: ConnectorResource | null;
  previewResult: ConnectorResourceTestResult | null;
  onCreateSync?: (resourceName: string) => void;
}

export function RestResourceSelectionPane({
  resource,
  previewResult,
  onCreateSync,
}: RestResourceSelectionPaneProps) {
  return (
    <aside className="flex w-72 shrink-0 flex-col border-l">
      <div className="flex h-10 items-center gap-2 border-b px-3">
        <Table2 className="size-3.5 text-primary" />
        <span className="text-xs font-semibold">동기화할 리소스</span>
      </div>
      {!resource ? (
        <div className="p-3 text-[11px] text-muted-foreground">
          리소스를 선택하면 동기화 구성을 확인할 수 있습니다.
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <div className="rounded border bg-muted/20 p-2.5">
              <div className="flex items-center gap-2">
                <Table2 className="size-3.5 text-primary" />
                <span className="text-xs font-semibold">
                  {resource.resourceName}
                </span>
              </div>
              <div className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                {resource.resourcePath}
              </div>
            </div>
            <dl className="mt-3 divide-y divide-border/60 text-[11px]">
              <div className="grid grid-cols-[92px_1fr] gap-2 py-2">
                <dt className="text-muted-foreground">목적지 Dataset</dt>
                <dd className="min-w-0 break-all font-mono">
                  {resource.datasetRef}
                </dd>
              </div>
              <div className="grid grid-cols-[92px_1fr] gap-2 py-2">
                <dt className="text-muted-foreground">기본 키</dt>
                <dd className="flex min-w-0 items-center gap-1 font-mono">
                  <KeyRound className="size-3 text-muted-foreground" />
                  {resource.primaryKey.join(", ") || "미지정"}
                </dd>
              </div>
              <div className="grid grid-cols-[92px_1fr] gap-2 py-2">
                <dt className="text-muted-foreground">스키마 선언</dt>
                <dd className="min-w-0">
                  {resource.schemaColumns.length} columns
                </dd>
              </div>
              <div className="grid grid-cols-[92px_1fr] gap-2 py-2">
                <dt className="text-muted-foreground">트랜잭션</dt>
                <dd className="min-w-0">SNAPSHOT 기본값</dd>
              </div>
            </dl>
            <div className="mt-3 rounded border px-2.5 py-2 text-[11px]">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">연결 검증</span>
                <StatusPill
                  intent={
                    previewResult?.status.toLowerCase() === "succeeded"
                      ? "success"
                      : "neutral"
                  }
                >
                  {previewResult?.status.toLowerCase() === "succeeded"
                    ? "확인됨"
                    : "미실행"}
                </StatusPill>
              </div>
              <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                동기화 생성 전 미리보기로 인증·네트워크·응답 스키마를 확인하는
                것을 권장합니다.
              </p>
            </div>
          </div>
          <div className="border-t p-3">
            <Button
              className="w-full"
              disabled={!onCreateSync}
              onClick={() => onCreateSync?.(resource.resourceName)}
            >
              이 리소스로 동기화 만들기
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        </div>
      )}
    </aside>
  );
}
