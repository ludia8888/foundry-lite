import type { OntologyCatalog } from "@foundry-lite/sdk";
import type { OntologyBuilderStepId } from "@foundry-lite/sdk/screen-recipes";
import { ontologyBuilderNavigation } from "@foundry-lite/sdk/screen-recipes";

import { StatusPill } from "@/components/shared/StatusPill";
import { Separator } from "@/components/ui/separator";

import { formatDateTime } from "../lib/ontology-view";

const BUILDER_STEP_LABELS: Record<OntologyBuilderStepId, string> = {
  dataset_explorer: "데이터셋 탐색",
  draft_builder: "드래프트 작성",
  validate: "검증",
  impact_plan: "영향 · 리인덱스 계획",
  proposal: "제안 제출",
  review: "리뷰",
  execute: "실행(머지)",
  reindex_status: "리인덱스 상태",
  sdk_drift: "SDK 드리프트 점검",
};

const BUILDER_STEP_DESCRIPTIONS: Record<OntologyBuilderStepId, string> = {
  dataset_explorer: "데이터셋 스키마를 확인하고 백킹 컬럼을 고릅니다.",
  draft_builder: "객체/속성/링크/액션을 드래프트로 설계합니다.",
  validate: "활성 온톨로지 기준으로 드래프트를 검증합니다.",
  impact_plan: "차단 변경·경고·리인덱스·SDK 영향을 검토합니다.",
  proposal: "드래프트를 변경 제안으로 제출합니다.",
  review: "리뷰어를 지정하고 승인/반려를 기록합니다.",
  execute: "승인된 제안을 다음 활성 버전으로 적용합니다.",
  reindex_status: "필요한 객체 리인덱스를 재실행하고 추적합니다.",
  sdk_drift: "생성된 OSDK 패키지와 새 카탈로그를 비교합니다.",
};

interface OverviewSectionProps {
  catalog: OntologyCatalog;
  propertyCount: number;
}

/** 개요: 온톨로지 메타데이터 카드 + 거버넌스 빌더 9단계 카드. */
export function OverviewSection({
  catalog,
  propertyCount,
}: OverviewSectionProps) {
  const navigation = ontologyBuilderNavigation();
  return (
    <div className="space-y-3">
      <div className="rounded border bg-card">
        <div className="p-3 text-[13px] font-semibold">온톨로지 메타데이터</div>
        <Separator />
        <div className="grid gap-x-8 gap-y-2 p-3 sm:grid-cols-2">
          <div>
            <div className="section-label">표시 이름</div>
            <div className="mt-0.5 text-xs">기본 온톨로지</div>
          </div>
          <div>
            <div className="section-label">설명</div>
            <div className="mt-0.5 text-xs">
              테넌트 기본 온톨로지 — 데이터셋을 업무 객체 모델로 연결합니다.
            </div>
          </div>
          <div>
            <div className="section-label">활성 버전</div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <span className="font-mono text-[11px]">
                v{catalog.versionNumber}
              </span>
              <StatusPill
                intent={catalog.status === "active" ? "success" : "neutral"}
              >
                {catalog.status === "active" ? "활성" : catalog.status}
              </StatusPill>
            </div>
          </div>
          <div>
            <div className="section-label">활성화 시각</div>
            <div className="mt-0.5 text-xs">
              {formatDateTime(catalog.activatedAt)}
            </div>
          </div>
          <div>
            <div className="section-label">리소스 수</div>
            <div className="mt-0.5 font-mono text-[11px]">
              객체 {catalog.objectTypes.length} · 링크{" "}
              {catalog.linkTypes.length} · 속성 {propertyCount}
            </div>
          </div>
        </div>
        <Separator />
        <div className="flex items-center gap-3 bg-muted/40 p-3">
          <span className="section-label shrink-0">버전 ID</span>
          <span className="truncate font-mono text-[11px]">
            {catalog.ontologyVersionId}
          </span>
        </div>
      </div>

      <div className="rounded border bg-card">
        <div className="flex items-center justify-between p-3">
          <span className="text-[13px] font-semibold">
            거버넌스 빌더 플로우
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {navigation.stepCount}단계
          </span>
        </div>
        <Separator />
        <ol className="p-3">
          {navigation.steps.map((step) => (
            <li key={step.id} className="flex items-start gap-2.5 py-1.5">
              <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border border-primary/40 bg-primary/10 text-[10px] font-semibold text-primary">
                {step.stepNumber}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium">
                  {BUILDER_STEP_LABELS[step.id]}
                </div>
                <div className="text-[11px] text-muted-foreground">
                  {BUILDER_STEP_DESCRIPTIONS[step.id]}
                </div>
              </div>
              <span className="hidden shrink-0 font-mono text-[10px] text-muted-foreground lg:inline">
                {step.reactHook}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
