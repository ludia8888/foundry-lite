import type {
  FoundryLiteOperationsRunPhase,
  FoundryLiteRecoveryPhase,
} from "@foundry-lite/sdk/react";

import type { StatusIntent } from "@/components/shared/StatusPill";

/** run phase → intent 필 색. */
export function runPhaseIntent(
  phase: FoundryLiteOperationsRunPhase,
): StatusIntent {
  switch (phase) {
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "running":
      return "info";
    case "pending":
      return "warning";
    default:
      return "neutral";
  }
}

const RUN_PHASE_LABELS: Record<FoundryLiteOperationsRunPhase, string> = {
  succeeded: "성공",
  failed: "실패",
  running: "실행 중",
  pending: "대기",
  unknown: "알 수 없음",
};

export function runPhaseLabel(phase: FoundryLiteOperationsRunPhase): string {
  return RUN_PHASE_LABELS[phase];
}

const RUN_TYPE_LABELS: Record<string, string> = {
  source_exploration: "Source Explorer",
  sync: "Sync",
  transform: "Transform",
  index: "Index",
  action: "Action",
  action_writeback: "Writeback",
  materialization: "Materialization",
  outbox: "Outbox",
  dead_letter: "Dead Letter",
  workflow: "Workflow",
  ai: "AIP",
  audit: "Audit",
};

export function runTypeLabel(runType: string): string {
  return RUN_TYPE_LABELS[runType] ?? runType;
}

const RECOVERY_PHASE: Record<
  FoundryLiteRecoveryPhase,
  { label: string; intent: StatusIntent }
> = {
  normal: { label: "정상", intent: "success" },
  preflight_blocked: { label: "Preflight 차단", intent: "danger" },
  restore_paused: { label: "복원 일시정지", intent: "warning" },
  resume_ready: { label: "재개 승인됨", intent: "info" },
  action_required: { label: "조치 필요", intent: "warning" },
  unknown: { label: "미확인", intent: "neutral" },
};

export function recoveryPhaseMeta(phase: FoundryLiteRecoveryPhase): {
  label: string;
  intent: StatusIntent;
} {
  return RECOVERY_PHASE[phase];
}

const SEVERITY: Record<string, { label: string; intent: StatusIntent }> = {
  critical: { label: "치명", intent: "danger" },
  warning: { label: "경고", intent: "warning" },
  info: { label: "정보", intent: "info" },
};

export function severityMeta(severity: string): {
  label: string;
  intent: StatusIntent;
} {
  return SEVERITY[severity] ?? { label: severity, intent: "neutral" };
}

const DLQ_STATUS: Record<string, { label: string; intent: StatusIntent }> = {
  QUARANTINED: { label: "격리", intent: "warning" },
  REPLAY_REQUESTED: { label: "재시도 요청", intent: "info" },
  REPLAYING: { label: "재시도 중", intent: "info" },
  RESOLVED: { label: "해결", intent: "success" },
  DISCARDED: { label: "폐기", intent: "neutral" },
};

export function dlqStatusMeta(status: string): {
  label: string;
  intent: StatusIntent;
} {
  return DLQ_STATUS[status] ?? { label: status, intent: "neutral" };
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

/** RuntimeRow(임의 JSON)에서 문자열 값을 안전하게 추출한다. */
export function readRowString(
  row: Record<string, unknown> | null | undefined,
  ...keys: string[]
): string | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.length > 0) return value;
    if (typeof value === "number") return String(value);
  }
  return null;
}

export function readRowNumber(
  row: Record<string, unknown> | null | undefined,
  ...keys: string[]
): number | null {
  if (!row) return null;
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes))
    return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}
