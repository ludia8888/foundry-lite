import {
  ArrowRight,
  Building2,
  EyeOff,
  ScanSearch,
  Shield,
} from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";

/** manage-markings 레퍼런스의 다크 네이비 마킹 배지(#3a3e49) + 흰 텍스트 + 방패. */
const MARKING_SAMPLES: readonly { label: string; outlined?: boolean }[] = [
  { label: "cohort 1" },
  { label: "cohort 2" },
  { label: "COVID-19 research", outlined: true },
  { label: "internal records" },
];

function MarkingBadge({
  label,
  outlined,
}: {
  label: string;
  outlined?: boolean;
}) {
  return (
    <span
      className={
        outlined
          ? "inline-flex items-center gap-1 rounded-full border border-[#3a3e49] bg-white px-2 py-0.5 text-[11px] font-medium text-[#3a3e49]"
          : "inline-flex items-center gap-1 rounded-full bg-[#3a3e49] px-2 py-0.5 text-[11px] font-medium text-white"
      }
    >
      <Shield className="size-3" />
      {label}
    </span>
  );
}

/**
 * Markings / 민감 데이터 탭.
 *
 * Palantir markings 엔진 / 민감 데이터 스캐너는 backend 부재(future_gap)다.
 * 레퍼런스(manage-markings, data lineage 전파, sensitive data banner)의 구조를
 * future 카드로 정직하게 표현하고, 실동작하지 않음을 명시한다. 이미지 근거의
 * 마킹 배지/카테고리/전파 다이어그램 형태를 반영한다.
 */
export function MarkingsFuturePanel() {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 p-3">
        <EyeOff className="mt-0.5 size-4 shrink-0 text-amber-600" />
        <div className="space-y-0.5">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-amber-900">
              민감 데이터 거버넌스는 참조(reference) 화면입니다
            </span>
            <StatusPill intent="warning">future</StatusPill>
          </div>
          <p className="text-[11px] text-amber-800">
            아래 구성은 Palantir 레퍼런스 구조를 보여주기 위한 것으로, 아직
            백엔드가 없어 실제로 동작하지 않습니다. 현재 실동작하는 권한 제어는
            프로젝트 권한 탭의 project role grant입니다.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="space-y-2 rounded border bg-card p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Shield className="size-4 text-muted-foreground" />
              <span className="text-[13px] font-semibold">Markings</span>
            </div>
            <StatusPill intent="neutral">future</StatusPill>
          </div>
          <p className="text-[11px] text-muted-foreground">
            분류 기반 접근 제어(classification-based access control)에 사용되는
            보안 마킹입니다. 마킹 카테고리는 conjunctive(AND) 또는
            disjunctive(OR)로 조합됩니다.
          </p>
          <div className="space-y-1.5 rounded border bg-muted/30 p-2.5">
            <span className="section-label">basic 카테고리 (예시)</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {MARKING_SAMPLES.map((marking) => (
                <MarkingBadge
                  key={marking.label}
                  label={marking.label}
                  outlined={marking.outlined}
                />
              ))}
            </div>
          </div>
        </section>

        <section className="space-y-2 rounded border bg-card p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <ArrowRight className="size-4 text-muted-foreground" />
              <span className="text-[13px] font-semibold">마킹 전파</span>
            </div>
            <StatusPill intent="neutral">future</StatusPill>
          </div>
          <p className="text-[11px] text-muted-foreground">
            상류 데이터셋의 마킹은 변환(transform)을 통해 하류 리소스로 전파되어
            보기 요구사항(view requirements)이 유지됩니다.
          </p>
          <div className="flex items-center gap-2 rounded border bg-muted/30 p-2.5">
            <span className="rounded border bg-card px-2 py-1 text-[11px]">
              원천 데이터셋
            </span>
            <ArrowRight className="size-3.5 text-muted-foreground" />
            <span className="rounded border bg-card px-2 py-1 text-[11px]">
              변환
            </span>
            <ArrowRight className="size-3.5 text-muted-foreground" />
            <span className="rounded border bg-card px-2 py-1 text-[11px]">
              하류 리소스
            </span>
            <MarkingBadge label="internal records" />
          </div>
        </section>

        <section className="space-y-2 rounded border bg-card p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <ScanSearch className="size-4 text-muted-foreground" />
              <span className="text-[13px] font-semibold">
                민감 데이터 스캐너
              </span>
            </div>
            <StatusPill intent="neutral">future</StatusPill>
          </div>
          <p className="text-[11px] text-muted-foreground">
            데이터셋 열을 스캔해 PII 같은 민감 데이터를 탐지하고 매치 조건에
            따라 마킹을 자동 적용합니다. Cipher 암호화 연동도 이 영역에
            속합니다.
          </p>
          <div className="flex items-start gap-2 rounded border border-dashed p-2.5">
            <ScanSearch className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <p className="text-[11px] text-muted-foreground">
              민감 데이터 스캔 생성, 매치 조건, 스캔 결과 리뷰 UI는 backend가
              준비되면 구현됩니다.
            </p>
          </div>
        </section>

        <section className="space-y-2 rounded border bg-card p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Building2 className="size-4 text-muted-foreground" />
              <span className="text-[13px] font-semibold">
                조직 &amp; 스페이스
              </span>
            </div>
            <StatusPill intent="neutral">future</StatusPill>
          </div>
          <p className="text-[11px] text-muted-foreground">
            조직/스페이스 관리, 그룹 간 협업, 게스트 멤버, 조직 권한은 platform
            security management 영역이며 아직 지원되지 않습니다.
          </p>
          <div className="flex items-start gap-2 rounded border border-dashed p-2.5">
            <Building2 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
            <p className="text-[11px] text-muted-foreground">
              organization/space admin은 future_gap입니다. 현재는 단일 tenant(
              <span className="font-mono">tenant-demo</span>) 컨텍스트만
              사용합니다.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
