import { Info, LayoutGrid, Search, Store } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { SCREENS } from "@/lib/screens";
import { cn } from "@/lib/utils";

import {
  buildCatalog,
  productsForCategory,
  type MarketplaceCategory,
  type MarketplaceProduct,
} from "./marketplace-model";
import { ProductCard } from "./ProductCard";
import { ProductDetailPanel } from "./ProductDetailPanel";
import { useSourceTemplateCatalog } from "./use-marketplace-queries";

const CATEGORIES: readonly {
  id: MarketplaceCategory;
  label: string;
}[] = [
  { id: "all", label: "전체" },
  { id: "data-connection", label: "데이터 연결 제품" },
  { id: "platform-app", label: "플랫폼 앱" },
];

/**
 * Marketplace (/marketplace) — reference_only.
 *
 * 레퍼런스 스토어 구조(카테고리 사이드바 + 제품 카드 그리드 + 상세)를 실존
 * 리소스로 채운다. 데이터 연결 제품은 sources.templates.list()(실 SDK), 플랫폼
 * 앱은 화면 레지스트리의 구현된 앱들이다. 설치는 백엔드가 없어 future로 분리한다.
 */
export default function MarketplacePage() {
  const templatesQuery = useSourceTemplateCatalog();
  const [category, setCategory] = useState<MarketplaceCategory>("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const templates = useMemo(
    () => templatesQuery.data ?? [],
    [templatesQuery.data],
  );

  const catalog = useMemo(() => buildCatalog(templates, SCREENS), [templates]);

  const products = useMemo(() => {
    const scoped = productsForCategory(catalog, category);
    const query = search.trim().toLowerCase();
    if (!query) return scoped;
    return scoped.filter(
      (product) =>
        product.name.toLowerCase().includes(query) ||
        product.subtitle.toLowerCase().includes(query) ||
        product.capabilities.some((capability) =>
          capability.toLowerCase().includes(query),
        ),
    );
  }, [catalog, category, search]);

  const selectedProduct =
    catalog.all.find((product) => product.id === selectedId) ?? null;

  const selectedTemplate =
    selectedProduct?.kind === "data-connection"
      ? (templates.find(
          (template) => `source:${template.sourceType}` === selectedProduct.id,
        ) ?? null)
      : null;

  const handleSelect = (product: MarketplaceProduct) => {
    setSelectedId(product.id);
  };

  const countByCategory = (id: MarketplaceCategory) =>
    productsForCategory(catalog, id).length;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b px-4 pt-3 pb-3">
        <PageHeader
          title="Marketplace"
          description="템플릿·솔루션·데이터 연결 제품 레퍼런스 스토어"
          actions={<RequestTelemetry />}
        />
        <div className="flex items-start gap-2 rounded border border-primary/20 bg-primary/5 px-3 py-2">
          <Info className="mt-0.5 size-3.5 shrink-0 text-primary" />
          <p className="text-[11px] text-foreground/80">
            <span className="font-medium">reference_only.</span> Foundry-lite
            marketplace는 레퍼런스 패턴입니다. 카드는 실존 리소스(소스 템플릿 ·
            구현된 플랫폼 앱)로 채워지고, 소스 템플릿은 새 소스 flow로
            재사용되며, 실제 설치 워크플로우는{" "}
            <Badge
              variant="secondary"
              className="px-1 py-0 align-middle text-[10px]"
            >
              future
            </Badge>{" "}
            로 분리됩니다.
          </p>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <div className="flex min-h-full flex-col lg:h-full lg:flex-row">
        {/* 카테고리 사이드바 */}
        <nav className="flex w-full shrink-0 flex-col gap-3 border-b p-3 lg:w-56 lg:border-r lg:border-b-0">
          <div className="space-y-1">
            <span className="section-label">카테고리</span>
            <ul className="space-y-0.5">
              {CATEGORIES.map((item) => {
                const isActive = item.id === category;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => setCategory(item.id)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-[13px] transition-colors",
                        isActive
                          ? "bg-accent font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted/60",
                      )}
                    >
                      <span className="truncate">{item.label}</span>
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {countByCategory(item.id)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
          <div className="space-y-1 rounded border bg-muted/30 p-2">
            <span className="section-label">레퍼런스 근거</span>
            <p className="text-[11px] text-muted-foreground">
              데이터 연결 제품은{" "}
              <span className="font-mono">sources.templates.list</span> 응답을,
              플랫폼 앱은 화면 레지스트리를 카드화합니다.
            </p>
          </div>
        </nav>

        {/* 제품 그리드 */}
        <div className="flex w-full shrink-0 flex-col border-b lg:w-[26rem] lg:border-r lg:border-b-0">
          <div className="border-b p-3">
            <div className="relative">
              <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="제품 검색"
                className="h-8 pl-7 text-xs"
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {templatesQuery.isLoading ? (
              <LoadingState rowCount={6} />
            ) : templatesQuery.error && templates.length === 0 ? (
              <ErrorState
                error={templatesQuery.error}
                onRetry={() => void templatesQuery.reload()}
              />
            ) : products.length === 0 ? (
              <EmptyState
                icon={LayoutGrid}
                title="제품이 없습니다"
                description="다른 카테고리를 선택하거나 검색어를 지우세요."
              />
            ) : (
              <div className="grid grid-cols-1 gap-2">
                {products.map((product) => (
                  <ProductCard
                    key={product.id}
                    product={product}
                    isSelected={product.id === selectedId}
                    onSelect={handleSelect}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* 상세 */}
        {selectedProduct ? (
          <ProductDetailPanel
            key={selectedProduct.id}
            product={selectedProduct}
            template={selectedTemplate}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              icon={Store}
              title="제품을 선택하세요"
              description="카테고리를 탐색하고 카드를 선택하면 상세와 사용 경로를 볼 수 있습니다."
            />
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
