import {
  classifyFoundryLiteError,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { GitCompareArrows, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";

import { StatusPill } from "./StatusPill";

interface StaleObjectVersionNoticeProps {
  error: unknown;
  onRefresh: () => void;
  isResolved?: boolean;
}

type StaleObjectVersionDetails = {
  expected: string | null;
  current: string | null;
  currentNumber: number | null;
};

function readVersionDisplay(
  details: Record<string, unknown>,
  ...keys: string[]
): string | null {
  for (const key of keys) {
    const value = details[key];
    if (typeof value === "number" && Number.isFinite(value)) return `v${value}`;
    if (typeof value === "string" && value.length > 0) return value;
  }
  return null;
}

function readVersionNumber(
  details: Record<string, unknown>,
  ...keys: string[]
): number | null {
  for (const key of keys) {
    const value = details[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && value.length > 0) {
      const parsed = Number(value.replace(/^v/i, ""));
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return null;
}

export function isStaleObjectVersionError(error: unknown): boolean {
  return classifyFoundryLiteError(normalizeFoundryLiteError(error)).kind ===
    "stale_object_version";
}

export function staleObjectVersionDetails(
  error: unknown,
): StaleObjectVersionDetails {
  const normalized = normalizeFoundryLiteError(error);
  return {
    expected: readVersionDisplay(
      normalized.details,
      "expectedObjectVersion",
      "expected_object_version",
    ),
    current: readVersionDisplay(
      normalized.details,
      "currentObjectVersion",
      "actualObjectVersion",
      "objectVersion",
      "current_object_version",
    ),
    currentNumber: readVersionNumber(
      normalized.details,
      "currentObjectVersion",
      "actualObjectVersion",
      "objectVersion",
      "current_object_version",
    ),
  };
}

/**
 * Action apply 충돌 전용 복구 안내.
 * 백엔드가 내려준 expected/current version evidence를 사용자에게 그대로 보여준다.
 */
export function StaleObjectVersionNotice({
  error,
  onRefresh,
  isResolved = false,
}: StaleObjectVersionNoticeProps) {
  const normalized = normalizeFoundryLiteError(error);
  const classification = classifyFoundryLiteError(normalized);
  if (classification.kind !== "stale_object_version") return null;
  if (isResolved) return null;

  const { expected, current } = staleObjectVersionDetails(normalized);

  return (
    <div className="rounded border border-warning/40 bg-warning/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompareArrows className="size-4 text-warning" />
        <StatusPill intent="warning">stale object version</StatusPill>
        <span className="font-mono text-[11px] text-muted-foreground">
          expected={expected ?? "?"} / current={current ?? "?"}
        </span>
      </div>
      <p className="mt-2 text-[12px] leading-5 text-foreground/80">
        다른 작업이 먼저 이 객체를 변경했습니다. 현재 버전을 다시 불러온 뒤
        새 expectedObjectVersion으로 실행해야 합니다.
      </p>
      <Button size="sm" variant="outline" className="mt-2" onClick={onRefresh}>
        <RefreshCw />
        현재 버전 새로고침
      </Button>
    </div>
  );
}
