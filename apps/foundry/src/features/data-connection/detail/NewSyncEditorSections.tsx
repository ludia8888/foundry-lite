import type { ConnectorResource } from "@foundry-lite/sdk";
import { Activity, CalendarClock, Database, FileSearch } from "lucide-react";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import type {
  NewSyncDraft,
  NewSyncDraftUpdater,
  ScheduleMode,
} from "./new-sync-model";
import { findRestResource } from "./new-sync-model";
import { TRANSACTION_MODES } from "./sync-config";

interface DraftSectionProps {
  draft: NewSyncDraft;
  updateDraft: NewSyncDraftUpdater;
}

export function CoreConfigurationCard({
  draft,
  updateDraft,
  syncName,
  isDatasetValid,
  selectedRestResource,
}: DraftSectionProps & {
  syncName: string;
  isDatasetValid: boolean;
  selectedRestResource: ConnectorResource | null;
}) {
  return (
    <EditorCard icon={Database} title="핵심 구성">
      <div className="space-y-3">
        <div className="space-y-1">
          <Label className="text-[11px]">동기화 이름</Label>
          <Input
            data-testid="new-sync-display-name"
            value={draft.displayName}
            onChange={(event) =>
              updateDraft("displayName", event.target.value)
            }
            placeholder="예: 주문 테이블 동기화"
            className="h-7 text-xs"
          />
          {draft.displayName ? (
            <div className="font-mono text-[10px] text-muted-foreground">
              sync_name={syncName || "(영문/숫자/_ 필요)"}
            </div>
          ) : null}
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">목적지 데이터셋</Label>
          <Input
            data-testid="new-sync-dataset-ref"
            value={draft.datasetRef}
            onChange={(event) => {
              updateDraft("isDatasetRefTouched", true);
              updateDraft("datasetRef", event.target.value);
            }}
            placeholder="namespace.name"
            className={cn(
              "h-7 font-mono text-xs",
              draft.datasetRef && !isDatasetValid && "border-destructive",
            )}
          />
          <div className="text-[10px] text-muted-foreground">
            동기화가 생성할 Foundry 데이터셋 위치입니다.
          </div>
          {selectedRestResource ? (
            <div className="text-[10px] text-muted-foreground">
              REST 리소스{" "}
              <span className="font-mono">
                {selectedRestResource.resourceName}
              </span>
              의 datasetRef와 자동으로 맞춥니다.
            </div>
          ) : null}
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">트랜잭션 유형</Label>
          <div className="space-y-1.5">
            {TRANSACTION_MODES.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => updateDraft("mode", option.value)}
                className={cn(
                  "flex w-full items-start gap-2 rounded border p-2 text-left",
                  draft.mode === option.value
                    ? "border-primary bg-accent"
                    : "hover:bg-muted/60",
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 size-3 shrink-0 rounded-full border",
                    draft.mode === option.value
                      ? "border-4 border-primary"
                      : "border-muted-foreground/40",
                  )}
                />
                <span className="text-[11px] font-medium">
                  {option.label}
                  <span className="block text-[10px] font-normal text-muted-foreground">
                    {option.description}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </EditorCard>
  );
}

export function ScheduleConfigurationCard({
  draft,
  updateDraft,
}: DraftSectionProps) {
  return (
    <EditorCard icon={CalendarClock} title="일정">
      <div className="space-y-2">
        <Select
          value={draft.scheduleMode}
          onValueChange={(value) =>
            updateDraft("scheduleMode", value as ScheduleMode)
          }
        >
          <SelectTrigger size="sm" className="w-full text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="manual">수동 실행</SelectItem>
            <SelectItem value="interval">간격 (interval)</SelectItem>
            <SelectItem value="cron">cron</SelectItem>
          </SelectContent>
        </Select>
        {draft.scheduleMode === "interval" ? (
          <div className="flex items-center gap-2">
            <Input
              value={draft.everySecondsText}
              onChange={(event) =>
                updateDraft("everySecondsText", event.target.value)
              }
              className="h-7 w-24 font-mono text-xs"
              inputMode="numeric"
            />
            <span className="text-[11px] text-muted-foreground">
              초마다 실행
            </span>
          </div>
        ) : null}
        {draft.scheduleMode === "cron" ? (
          <Input
            value={draft.cronText}
            onChange={(event) => updateDraft("cronText", event.target.value)}
            className="h-7 font-mono text-xs"
            placeholder="분 시 일 월 요일"
          />
        ) : null}
        <div className="text-[10px] text-muted-foreground">
          새로 생성된 동기화에 일정을 설정하는 것이 좋습니다.
        </div>
      </div>
    </EditorCard>
  );
}

export function PostgresSourceConfiguration({
  draft,
  updateDraft,
}: DraftSectionProps) {
  return (
    <EditorCard icon={Database} title="소스별 구성 — 테이블">
      <div className="space-y-3">
        <div className="space-y-1">
          <Label className="text-[11px]">테이블 이름</Label>
          <Input
            value={draft.tableName}
            onChange={(event) => updateDraft("tableName", event.target.value)}
            placeholder="예: erp_orders"
            className="h-7 font-mono text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">자격 증명 (secret ref)</Label>
          <Input
            value={draft.secretRef}
            onChange={(event) => updateDraft("secretRef", event.target.value)}
            placeholder="데이터베이스 URL secret ref"
            className="h-7 font-mono text-xs"
          />
          <div className="text-[10px] text-muted-foreground">
            소스에 저장된 자격 증명 ref가 자동으로 채워집니다.
          </div>
        </div>
      </div>
    </EditorCard>
  );
}

export function RestSourceConfiguration({
  draft,
  updateDraft,
  resources,
  selectedResource,
  hasResourceMismatch,
  error,
  onRetry,
}: DraftSectionProps & {
  resources: readonly ConnectorResource[];
  selectedResource: ConnectorResource | null;
  hasResourceMismatch: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <EditorCard icon={Database} title="소스별 구성 — REST 리소스">
      <div className="space-y-3">
        <div className="space-y-1">
          <Label className="text-[11px]">커넥터 이름</Label>
          <Input
            value={draft.connectorName}
            onChange={(event) => {
              updateDraft("connectorName", event.target.value);
              updateDraft("resourceName", "");
            }}
            className="h-7 font-mono text-xs"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-[11px]">리소스 이름</Label>
          {resources.length > 0 ? (
            <Select
              value={draft.resourceName}
              onValueChange={(value) => {
                updateDraft("resourceName", value);
                const resource = findRestResource(resources, value);
                if (resource) {
                  updateDraft("datasetRef", resource.datasetRef);
                  updateDraft("isDatasetRefTouched", false);
                }
              }}
            >
              <SelectTrigger
                size="sm"
                data-testid="new-sync-rest-resource"
                className="w-full font-mono text-xs"
              >
                <SelectValue placeholder="리소스를 선택하세요" />
              </SelectTrigger>
              <SelectContent>
                {resources.map((resource) => (
                  <SelectItem
                    key={resource.resourceName}
                    value={resource.resourceName}
                  >
                    {resource.resourceName} · {resource.datasetRef}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              value={draft.resourceName}
              onChange={(event) =>
                updateDraft("resourceName", event.target.value)
              }
              placeholder="예: orders"
              className="h-7 font-mono text-xs"
            />
          )}
          {selectedResource ? (
            <div className="font-mono text-[10px] text-muted-foreground">
              datasetRef={selectedResource.datasetRef}
            </div>
          ) : null}
          {hasResourceMismatch ? (
            <StatusPill intent="warning">
              리소스 datasetRef와 목적지 데이터셋이 달라 생성할 수 없습니다
            </StatusPill>
          ) : null}
          {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
        </div>
      </div>
    </EditorCard>
  );
}

export function SyncExecutionSummaryCards({
  draft,
  isPostgres,
  isKafka,
}: {
  draft: NewSyncDraft;
  isPostgres: boolean;
  isKafka: boolean;
}) {
  return (
    <>
      {isPostgres ? (
        <EditorCard icon={FileSearch} title="SQL 쿼리">
          <pre className="overflow-x-auto rounded bg-muted/60 p-2.5 font-mono text-[11px] leading-5">
            {`SELECT * FROM ${draft.tableName.trim() || "<테이블>"}`}
            {draft.isIncremental && draft.incrementalColumn.trim()
              ? `\nWHERE ${draft.incrementalColumn.trim()} > :lastValue`
              : ""}
          </pre>
          <div className="mt-1 text-[10px] text-muted-foreground">
            동기화에 포함된 테이블의 기본 쿼리는 SELECT * 입니다. 실행 쿼리는
            위 구성에서 파생됩니다.
          </div>
        </EditorCard>
      ) : null}
      {isKafka ? (
        <EditorCard icon={Activity} title="Streaming checkpoint">
          <p className="text-[11px] leading-5 text-muted-foreground">
            topic·partition·consumer group별 마지막 offset은 데이터셋 버전
            커밋이 성공한 뒤에만 전진합니다. 재시작은 커밋된 offset 다음
            레코드부터 이어집니다.
          </p>
        </EditorCard>
      ) : null}
    </>
  );
}

export function IncrementalConfiguration({
  draft,
  updateDraft,
  isPostgres,
  isKafka,
}: DraftSectionProps & { isPostgres: boolean; isKafka: boolean }) {
  return (
    <EditorCard
      icon={Database}
      title="Incremental"
      meta={
        <Switch
          checked={draft.isIncremental}
          onCheckedChange={(value) => updateDraft("isIncremental", value)}
          disabled={!isPostgres}
          aria-label="Incremental 활성화"
        />
      }
    >
      {draft.isIncremental && isPostgres ? (
        <div className="space-y-2">
          <p className="text-[11px] text-muted-foreground">
            타임스탬프나 id 같은 단조 증가 컬럼을 지정하면, 이미 가져온 마지막
            값(체크포인트) 이후의 행만 가져옵니다. 첫 실행은 전체를
            가져옵니다.
          </p>
          <Input
            value={draft.incrementalColumn}
            onChange={(event) =>
              updateDraft("incrementalColumn", event.target.value)
            }
            placeholder="체크포인트 컬럼 (예: updated_at)"
            className="h-7 font-mono text-xs"
          />
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          {isPostgres
            ? "비활성 — 매 실행마다 전체 데이터를 가져옵니다 (트랜잭션 유형에 따라 덮어쓰기/추가)."
            : isKafka
              ? "Kafka 동기화는 topic partition offset 체크포인트를 자동으로 사용합니다."
              : "REST 동기화는 커넥터의 커서 설정을 따릅니다."}
        </p>
      )}
    </EditorCard>
  );
}

export function EditorCard({
  icon: Icon,
  title,
  meta,
  children,
}: {
  icon: typeof Database;
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="flex items-center gap-1.5 text-[13px] font-semibold">
          <Icon className="size-3.5 text-muted-foreground" />
          {title}
        </span>
        {meta}
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}
