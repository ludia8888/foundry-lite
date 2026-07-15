import type { SourceTemplate } from "@foundry-lite/sdk";
import { ArrowUpRight, Download, ExternalLink } from "lucide-react";
import { Link } from "react-router";

import { StatusPill } from "@/components/shared/StatusPill";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import type { MarketplaceProduct } from "./marketplace-model";
import { capabilityLabel, modeLabel } from "./marketplace-model";
import { ProductDiagram } from "./ProductDiagram";

interface ProductDetailPanelProps {
  product: MarketplaceProduct;
  /** data-connection 제품일 때 원본 템플릿 (mode 상세 표시용). */
  template: SourceTemplate | null;
}

/** 제품 상세 페이지: 스크린샷 자리 + capability + 설치(future) + 실링크. */
export function ProductDetailPanel({
  product,
  template,
}: ProductDetailPanelProps) {
  const Icon = product.icon;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-4 p-4">
        <div className="flex items-start gap-3">
          <div className="flex size-11 shrink-0 items-center justify-center rounded border bg-muted/60">
            <Icon className="size-5.5 text-foreground/70" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-base font-semibold">
                {product.name}
              </h2>
              <StatusPill
                intent={product.kind === "data-connection" ? "info" : "neutral"}
              >
                {product.kind === "data-connection"
                  ? "데이터 연결 제품"
                  : "플랫폼 앱"}
              </StatusPill>
              {product.kind === "data-connection" ? (
                <StatusPill
                  intent={
                    product.executionStatus === "active"
                      ? "success"
                      : "warning"
                  }
                >
                  {product.executionStatus === "active"
                    ? "실행 가능"
                    : "정의만"}
                </StatusPill>
              ) : null}
            </div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              {product.subtitle}
            </div>
          </div>
        </div>

        <p className="text-[13px] text-foreground/80">{product.description}</p>

        <div className="flex flex-wrap items-center gap-2">
          {product.kind === "data-connection" &&
          product.executionStatus !== "active" ? (
            <Button size="sm" variant="outline" disabled>
              <ArrowUpRight className="size-3.5" /> {product.primaryLabel}
            </Button>
          ) : product.hasFutureInstall ? (
            <Button size="sm" variant="outline" disabled>
              <Download className="size-3.5" /> 설치
              <Badge variant="secondary" className="ml-1">
                future
              </Badge>
            </Button>
          ) : (
            <Button size="sm" asChild>
              <Link to={product.primaryHref}>
                <ArrowUpRight className="size-3.5" /> {product.primaryLabel}
              </Link>
            </Button>
          )}

          {product.kind === "platform-app" ? (
            <Button size="sm" variant="outline" asChild>
              <Link to={product.primaryHref}>
                <ExternalLink className="size-3.5" /> {product.primaryLabel}
              </Link>
            </Button>
          ) : null}
        </div>

        {product.kind === "data-connection" &&
        product.executionStatus !== "active" ? (
          <p className="text-[11px] text-muted-foreground">
            이 항목은 connector 메타데이터와 설계 경계만 제공합니다. 실행형
            adapter·탐색·sync가 연결되기 전에는 새 Source 실행을 시작할 수
            없습니다.
          </p>
        ) : !product.hasFutureInstall ? (
          <p className="text-[11px] text-muted-foreground">
            데이터 연결 제품은 Data Connection 위저드의 해당 소스 유형 flow로
            바로 이어집니다. 실제 marketplace 설치는 future로 분리되어 있습니다.
          </p>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            marketplace 설치 워크플로우(template registry · solution package
            install · billing)는 백엔드가 없어 future로 분리되어 있습니다.
            지금은 앱을 직접 열어 사용하세요.
          </p>
        )}

        <div className="space-y-1.5">
          <span className="section-label">스크린샷</span>
          <ProductDiagram nodes={product.diagramNodes} />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <span className="section-label">Capabilities</span>
            <div className="flex flex-wrap gap-1">
              {product.capabilities.map((capability) => (
                <span
                  key={capability}
                  className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
                >
                  {capability}
                </span>
              ))}
            </div>
          </div>

          {template ? (
            <TemplateModes template={template} />
          ) : product.kind === "data-connection" ? (
            <BuiltInDataConnectionEvidence product={product} />
          ) : (
            <div className="space-y-1.5">
              <span className="section-label">구성 근거</span>
              <p className="text-[11px] text-muted-foreground">
                화면 레지스트리의 구현된 앱입니다. 온톨로지·데이터 위에서 실행과
                액션 증거를 제공합니다.
              </p>
            </div>
          )}
        </div>

        {template ? (
          <div className="rounded border bg-muted/30 p-3">
            <span className="section-label">원본 근거</span>
            <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
              <div className="flex justify-between gap-2">
                <dt>source_type</dt>
                <dd className="text-foreground/80">{template.sourceType}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>exploration</dt>
                <dd className="text-foreground/80">
                  {template.supportsExploration ? "지원" : "미지원"}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>capabilities</dt>
                <dd className="text-foreground/80">
                  {template.capabilities.map(capabilityLabel).join(", ")}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>run_modes</dt>
                <dd className="text-foreground/80">
                  {template.managedRunModes.length}
                </dd>
              </div>
            </dl>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function BuiltInDataConnectionEvidence({
  product,
}: {
  product: MarketplaceProduct;
}) {
  return (
    <div className="space-y-1.5">
      <span className="section-label">구성 근거</span>
      <p className="text-[11px] text-muted-foreground">
        Foundry-lite에 내장된 Data Connection source flow입니다. 이 제품은
        템플릿 설치 없이 <span className="font-mono">{product.subtitle}</span>{" "}
        위저드로 바로 이동해 실제 backend commit을 실행합니다.
      </p>
    </div>
  );
}

/** 소스 템플릿의 credential / network / managed run 모드 (future 배지 포함). */
function TemplateModes({ template }: { template: SourceTemplate }) {
  const groups: readonly { label: string; codes: string[] }[] = [
    { label: "네트워크", codes: template.networkModes },
    { label: "자격 증명", codes: template.credentialModes },
    { label: "실행 모드", codes: template.managedRunModes },
  ];
  return (
    <div className="space-y-2">
      {groups.map((group) => (
        <div key={group.label} className="space-y-1">
          <span className="section-label">{group.label}</span>
          <div className="flex flex-wrap gap-1">
            {group.codes.map((code) => {
              const { label, isFuture } = modeLabel(code);
              return (
                <span
                  key={code}
                  className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
                >
                  {label}
                  {isFuture ? (
                    <Badge
                      variant="secondary"
                      className="px-1 py-0 text-[10px]"
                    >
                      future
                    </Badge>
                  ) : null}
                </span>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
