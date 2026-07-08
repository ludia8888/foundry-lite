import { ArrowRight } from "lucide-react";
import { Link } from "react-router";

import { StatusPill } from "@/components/shared/StatusPill";
import { SCREEN_GROUP_LABELS, SCREENS, type ScreenGroup } from "@/lib/screens";

const GROUP_ORDER: readonly ScreenGroup[] = [
  "data",
  "ontology",
  "apps",
  "operations",
];

/** Global Navigation 홈: 웰컴 배너 + 카테고리별 앱 런처 카드 그리드. */
export default function HomePage() {
  return (
    <div className="space-y-6 p-4">
      <div className="rounded bg-primary p-5 text-primary-foreground">
        <h1 className="text-lg font-semibold">
          Foundry에 오신 것을 환영합니다
        </h1>
        <p className="mt-1 text-xs opacity-90">
          데이터 연결부터 파이프라인, 온톨로지, 업무 앱까지 하나의 플랫폼에서
          작업하세요.
        </p>
        <Link
          to="/data/connections"
          className="mt-3 inline-flex items-center gap-1 rounded bg-white/15 px-2.5 py-1.5 text-xs font-medium hover:bg-white/25"
        >
          데이터 연결 시작하기
          <ArrowRight className="size-3.5" />
        </Link>
      </div>

      {GROUP_ORDER.map((group) => (
        <section key={group}>
          <h2 className="section-label mb-2">{SCREEN_GROUP_LABELS[group]}</h2>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {SCREENS.filter((screen) => screen.group === group).map(
              (screen) => (
                <Link
                  key={screen.id}
                  to={screen.route}
                  className="group flex items-start gap-3 rounded border bg-card p-3 transition-colors hover:border-primary/50"
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded bg-primary/10 text-primary">
                    <screen.icon className="size-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-[13px] font-medium text-primary group-hover:underline">
                        {screen.title}
                      </span>
                      {!screen.isImplemented ? (
                        <StatusPill intent="neutral">예정</StatusPill>
                      ) : screen.status === "partial" ? (
                        <StatusPill intent="warning">부분</StatusPill>
                      ) : screen.status === "reference_only" ? (
                        <StatusPill intent="neutral">참고</StatusPill>
                      ) : null}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                      {screen.description}
                    </p>
                  </div>
                </Link>
              ),
            )}
          </div>
        </section>
      ))}
    </div>
  );
}
