import type { SourceConnection, SourceManagedSync } from "@foundry-lite/sdk";
import { X } from "lucide-react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import {
  CoreConfigurationCard,
  IncrementalConfiguration,
  PostgresSourceConfiguration,
  RestSourceConfiguration,
  ScheduleConfigurationCard,
  SyncExecutionSummaryCards,
} from "./NewSyncEditorSections";
import {
  KafkaMonitoringConfiguration,
  KafkaSourceConfiguration,
} from "./NewSyncEditorKafkaSections";
import { useNewSyncEditorController } from "./use-new-sync-editor";

interface NewSyncEditorProps {
  source: SourceConnection;
  onCreated: (sync: SourceManagedSync) => void;
  onCancel: () => void;
  /** 탐색 탭에서 테이블 선택 후 진입한 경우의 초기값. */
  initialTableName?: string;
  /** REST Source 탐색 탭에서 리소스 선택 후 진입한 경우의 초기값. */
  initialResourceName?: string;
}

/**
 * 새 동기화 편집기 조정자.
 * 상태와 API 실행만 소유하고, 검증/payload 및 소스별 화면은 전용 모듈에 위임한다.
 */
export function NewSyncEditor({
  source,
  onCreated,
  onCancel,
  initialTableName,
  initialResourceName,
}: NewSyncEditorProps) {
  const controller = useNewSyncEditorController({
    source,
    initialTableName,
    initialResourceName,
    onCreated,
  });
  const {
    draft,
    updateDraft,
    kinds,
    syncName,
    idempotencyRef,
    validation,
    restResources,
    selectedRestResource,
    hasRestResourceMismatch,
  } = controller;

  if (!kinds.isRunnable) {
    return (
      <UnsupportedSyncType
        sourceType={source.kind}
        onCancel={onCancel}
      />
    );
  }

  return (
    <div className="space-y-4">
      <EditorHeader onCancel={onCancel} />
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3">
          <CoreConfigurationCard
            draft={draft}
            updateDraft={updateDraft}
            syncName={syncName}
            isDatasetValid={validation.isDatasetValid}
            selectedRestResource={selectedRestResource}
          />
          <ScheduleConfigurationCard
            draft={draft}
            updateDraft={updateDraft}
          />
        </div>

        <div className="space-y-3">
          {kinds.isPostgres ? (
            <PostgresSourceConfiguration
              draft={draft}
              updateDraft={updateDraft}
            />
          ) : null}
          {kinds.isRest ? (
            <RestSourceConfiguration
              draft={draft}
              updateDraft={updateDraft}
              resources={restResources}
              selectedResource={selectedRestResource}
              hasResourceMismatch={hasRestResourceMismatch}
              error={controller.restError}
              onRetry={controller.reloadRestResources}
            />
          ) : null}
          {kinds.isKafka ? (
            <>
              <KafkaSourceConfiguration
                draft={draft}
                updateDraft={updateDraft}
              />
              <KafkaMonitoringConfiguration
                draft={draft}
                updateDraft={updateDraft}
              />
            </>
          ) : null}
          <SyncExecutionSummaryCards
            draft={draft}
            isPostgres={kinds.isPostgres}
            isKafka={kinds.isKafka}
          />
          <IncrementalConfiguration
            draft={draft}
            updateDraft={updateDraft}
            isPostgres={kinds.isPostgres}
            isKafka={kinds.isKafka}
          />
          {controller.createError ? (
            <ErrorState error={controller.createError} />
          ) : null}
          <EditorActions
            idempotencyRef={idempotencyRef}
            canCreate={validation.canCreate}
            isRunning={controller.isCreating}
            onCancel={onCancel}
            onCreate={controller.create}
          />
          {!validation.isNameValid && draft.displayName ? (
            <StatusPill intent="warning">
              이름에서 유효한 식별자를 만들 수 없습니다
            </StatusPill>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function EditorHeader({ onCancel }: { onCancel: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="text-[15px] font-semibold">새 동기화</div>
        <div className="text-[11px] text-muted-foreground">
          소스에서 데이터를 읽어 Foundry 데이터셋으로 가져오는 작업을
          정의합니다.
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        aria-label="동기화 편집기 닫기"
        onClick={onCancel}
      >
        <X className="size-3.5" /> 취소
      </Button>
    </div>
  );
}

function UnsupportedSyncType({
  sourceType,
  onCancel,
}: {
  sourceType: string;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="text-[15px] font-semibold">새 동기화</div>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          <X className="size-3.5" /> 취소
        </Button>
      </div>
      <div className="rounded border bg-muted/40 p-3 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{sourceType}</span> 타입은
        반복 동기화 데이터플레인을 지원하지 않습니다. CSV·배치·미디어 업로드는
        업로드 시점에 즉시 커밋되고, 웹훅·CDC는 수신 시점에 처리됩니다.{" "}
        <StatusPill intent="neutral">future</StatusPill>
      </div>
    </div>
  );
}

function EditorActions({
  idempotencyRef,
  canCreate,
  isRunning,
  onCancel,
  onCreate,
}: {
  idempotencyRef: string;
  canCreate: boolean;
  isRunning: boolean;
  onCancel: () => void;
  onCreate: () => void;
}) {
  return (
    <div className="flex items-center justify-between rounded border bg-muted/40 px-3 py-2">
      <span className="font-mono text-[10px] text-muted-foreground">
        Idempotency-Key {idempotencyRef.slice(0, 24)}…
      </span>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          취소
        </Button>
        <Button
          size="sm"
          disabled={!canCreate || isRunning}
          onClick={onCreate}
        >
          {isRunning ? "생성 중..." : "동기화 생성"}
        </Button>
      </div>
    </div>
  );
}
