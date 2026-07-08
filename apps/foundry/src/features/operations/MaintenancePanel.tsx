import type { ObservabilityDetectRequest } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedIcebergMaintenancePlan,
  useFoundryLiteProvidedMaintenanceControls,
  useFoundryLiteProvidedObservabilityDetect,
} from "@foundry-lite/sdk/react";
import { Activity, Database, Play, RotateCw } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  EvidenceRow,
  FutureBadge,
  MutationEvidence,
  SectionHeader,
  StatTile,
} from "./OperationsPrimitives";
import {
  formatBytes,
  formatTimestamp,
  severityMeta,
} from "./operations-format";

/** 시드 데이터에서 결과를 만드는 기본 detector 세트 (cadence 초과 감지). */
const DEFAULT_DETECT_REQUEST: ObservabilityDetectRequest = {
  configs: [
    {
      detectorId: "transform-flow-interruption",
      detectorType: "flow_interruption",
      configVersion: "v1",
      owner: "ops_manager",
      severity: "critical",
      runType: "transform",
      expectedCadenceSeconds: 60,
    },
    {
      detectorId: "sync-flow-interruption",
      detectorType: "flow_interruption",
      configVersion: "v1",
      owner: "ops_manager",
      severity: "warning",
      runType: "sync",
      expectedCadenceSeconds: 60,
    },
    {
      detectorId: "ai-flow-interruption",
      detectorType: "flow_interruption",
      configVersion: "v1",
      owner: "ops_manager",
      severity: "warning",
      runType: "ai",
      expectedCadenceSeconds: 60,
    },
  ],
};

export function MaintenancePanel() {
  return (
    <div className="space-y-4">
      <ObservabilitySection />
      <IcebergSection />
      <IndexReplaySection />
    </div>
  );
}

function ObservabilitySection() {
  const detect = useFoundryLiteProvidedObservabilityDetect();

  const handleDetect = () => {
    void detect.execute(DEFAULT_DETECT_REQUEST);
  };

  return (
    <section className="rounded border bg-card p-3">
      <SectionHeader
        label="Observability 탐지"
        hint="flow_interruption / lag / skew / SLO detector 실행"
        actions={
          <Button size="sm" onClick={handleDetect} disabled={detect.isRunning}>
            <Activity className="size-3.5" /> detect 실행
          </Button>
        }
      />
      {detect.report ? (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatTile
              label="활성 인시던트"
              value={detect.activeIncidents.length}
              intent={detect.hasActiveIncidents ? "danger" : "success"}
            />
            <StatTile
              label="치명"
              value={detect.criticalIncidents.length}
              intent={detect.hasCriticalIncidents ? "danger" : "neutral"}
            />
            <StatTile label="경고" value={detect.warningIncidents.length} />
            <StatTile
              label="억제됨"
              value={detect.suppressedIncidents.length}
            />
          </div>
          <MutationEvidence>
            <span>config_version={detect.report.configurationVersion}</span>
            <span>
              generated_at={formatTimestamp(detect.report.generatedAt)}
            </span>
          </MutationEvidence>
          {detect.activeIncidents.length > 0 ? (
            <div className="space-y-1.5">
              {detect.activeIncidents.map((incident) => {
                const meta = severityMeta(incident.severity);
                return (
                  <div
                    key={incident.id}
                    className="rounded border bg-canvas px-3 py-2"
                  >
                    <div className="flex items-center gap-2">
                      <StatusPill intent={meta.intent}>{meta.label}</StatusPill>
                      <span className="text-[12px] font-medium">
                        {incident.detectorId}
                      </span>
                      <span className="ml-auto font-mono text-[10px] text-muted-foreground">
                        {incident.detectorType}
                      </span>
                    </div>
                    <p className="mt-1 text-[12px] text-foreground/90">
                      {incident.message}
                    </p>
                    <div className="mt-1 font-mono text-[10px] text-muted-foreground">
                      dedupe={incident.dedupeKey} · owner={incident.owner}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <StatusPill intent="success">활성 인시던트 없음</StatusPill>
          )}
        </div>
      ) : detect.error ? (
        <ErrorState
          error={detect.error}
          onRetry={handleDetect}
          className="mt-3"
        />
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          detector 세트를 실행해 스트림 중단·지연·왜곡·SLO 위반을 탐지합니다.
        </p>
      )}
    </section>
  );
}

function IcebergSection() {
  const [datasetRef, setDatasetRef] = useState("clean.orders");
  const [activeRef, setActiveRef] = useState<string | null>(null);
  const plan = useFoundryLiteProvidedIcebergMaintenancePlan({
    datasetRef: activeRef,
  });

  return (
    <section className="rounded border bg-card p-3">
      <SectionHeader
        label="Iceberg 유지보수 계획"
        hint="읽기 전용 — 스냅샷/compaction 후보 확인 (변경 없음)"
        actions={
          <div className="flex items-center gap-1.5">
            <Input
              value={datasetRef}
              onChange={(event) => setDatasetRef(event.target.value)}
              placeholder="namespace.name"
              className="h-8 w-48 font-mono text-[11px]"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => setActiveRef(datasetRef.trim() || null)}
              disabled={plan.isRefreshing || !datasetRef.trim()}
            >
              <Database className="size-3.5" /> 계획 읽기
            </Button>
          </div>
        }
      />
      {plan.error ? (
        <ErrorState
          error={plan.error}
          onRetry={() => void plan.reload()}
          className="mt-3"
        />
      ) : plan.plan ? (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatTile label="스냅샷" value={plan.snapshots.length} />
            <StatTile
              label="compaction 후보"
              value={plan.compactionCandidates.length}
              intent={plan.hasCompactionCandidates ? "warning" : "neutral"}
            />
            <StatTile
              label="orphan 스냅샷"
              value={plan.orphanSnapshots.length}
            />
            <StatTile
              label="후보 크기"
              value={formatBytes(plan.candidateBytes)}
            />
          </div>
          <dl className="divide-y rounded border bg-canvas px-3">
            <EvidenceRow
              label="현재 스냅샷 보호"
              value={plan.isCurrentSnapshotProtected ? "예" : "아니오"}
              mono={false}
            />
            <EvidenceRow
              label="보존 스냅샷"
              value={plan.retainedSnapshotIds.length}
            />
            <EvidenceRow
              label="삭제 가능 스냅샷"
              value={plan.deletableSnapshotIds.length}
            />
          </dl>
        </div>
      ) : activeRef ? (
        <p className="mt-3 text-xs text-muted-foreground">
          계획을 불러오는 중…
        </p>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          dataset ref를 입력하고 계획을 읽으면 스냅샷·compaction 후보를 확인할
          수 있습니다. 이 작업은 읽기 전용입니다.
        </p>
      )}
    </section>
  );
}

function IndexReplaySection() {
  const controls = useFoundryLiteProvidedMaintenanceControls();
  const [objectType, setObjectType] = useState("Order");
  const [failedRunId, setFailedRunId] = useState("");
  const [transformRunId, setTransformRunId] = useState("");

  const handleReplayObjectType = () => {
    const trimmed = objectType.trim();
    if (!trimmed) return;
    void controls.replayObjectTypeIndex.execute({ objectType: trimmed });
  };

  const handleReplayFailedRun = () => {
    const trimmed = failedRunId.trim();
    if (!trimmed) return;
    void controls.replayFailedIndexRun.execute({ runId: trimmed });
  };

  const handleRetryTransform = () => {
    const trimmed = transformRunId.trim();
    if (!trimmed) return;
    void controls.retryTransformRun.execute({ runId: trimmed });
  };

  return (
    <section className="rounded border bg-card p-3">
      <SectionHeader
        label="인덱스 replay · 변환 재시도"
        hint="객체 타입 인덱스 재구축, 실패 index run replay, 실패 transform run 재시도"
      />
      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        {/* object type reindex */}
        <div className="space-y-2 rounded border bg-canvas p-2.5">
          <span className="section-label">객체 타입 인덱스 재구축</span>
          <Input
            value={objectType}
            onChange={(event) => setObjectType(event.target.value)}
            placeholder="Order"
            className="h-8 font-mono text-[11px]"
          />
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            onClick={handleReplayObjectType}
            disabled={controls.replayObjectTypeIndex.isRunning}
          >
            <RotateCw className="size-3.5" /> 재구축
          </Button>
          {controls.replayObjectTypeIndex.result ? (
            <MutationEvidence>
              <span>
                run={controls.replayObjectTypeIndex.result.index_run_id}
              </span>
              <span>
                upserted=
                {controls.replayObjectTypeIndex.result.objects_upserted}
              </span>
            </MutationEvidence>
          ) : null}
          {controls.replayObjectTypeIndex.error ? (
            <ErrorState error={controls.replayObjectTypeIndex.error} />
          ) : null}
        </div>

        {/* failed index run replay */}
        <div className="space-y-2 rounded border bg-canvas p-2.5">
          <span className="section-label">실패 index run replay</span>
          <Input
            value={failedRunId}
            onChange={(event) => setFailedRunId(event.target.value)}
            placeholder="index_run_id"
            className="h-8 font-mono text-[11px]"
          />
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            onClick={handleReplayFailedRun}
            disabled={
              controls.replayFailedIndexRun.isRunning || !failedRunId.trim()
            }
          >
            <Play className="size-3.5" /> replay
          </Button>
          {controls.replayFailedIndexRun.result ? (
            <MutationEvidence>
              <span>
                run={controls.replayFailedIndexRun.result.index_run_id}
              </span>
            </MutationEvidence>
          ) : null}
          {controls.replayFailedIndexRun.error ? (
            <ErrorState error={controls.replayFailedIndexRun.error} />
          ) : null}
        </div>

        {/* transform retry */}
        <div className="space-y-2 rounded border bg-canvas p-2.5">
          <span className="section-label">실패 transform 재시도</span>
          <Input
            value={transformRunId}
            onChange={(event) => setTransformRunId(event.target.value)}
            placeholder="transform_run_id"
            className="h-8 font-mono text-[11px]"
          />
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            onClick={handleRetryTransform}
            disabled={
              controls.retryTransformRun.isRunning || !transformRunId.trim()
            }
          >
            <RotateCw className="size-3.5" /> 재시도
          </Button>
          {controls.retryTransformRun.result ? (
            <MutationEvidence>
              <span>
                new_run={controls.retryTransformRun.result.transform_run_id}
              </span>
              <span>rows={controls.retryTransformRun.result.row_count}</span>
            </MutationEvidence>
          ) : null}
          {controls.retryTransformRun.error ? (
            <ErrorState error={controls.retryTransformRun.error} />
          ) : null}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <span className="section-label">예정</span>
        <FutureBadge>자동 remediation policy</FutureBadge>
        <FutureBadge>SLA 대시보드</FutureBadge>
      </div>
    </section>
  );
}
