import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import type { MarketplaceProduct } from "./marketplace-model";

interface ProductCardProps {
  product: MarketplaceProduct;
  isSelected: boolean;
  onSelect: (product: MarketplaceProduct) => void;
}

/** 제품 카드: 아이콘 + 이름 + capability 칩 + 설명. */
export function ProductCard({
  product,
  isSelected,
  onSelect,
}: ProductCardProps) {
  const Icon = product.icon;
  return (
    <button
      type="button"
      onClick={() => onSelect(product)}
      className={cn(
        "flex h-full flex-col items-start gap-2 rounded border p-3 text-left transition-colors",
        isSelected
          ? "border-primary/40 bg-accent"
          : "border-border hover:border-primary/30 hover:bg-muted/50",
      )}
    >
      <div className="flex w-full items-start gap-2.5">
        <div className="flex size-9 shrink-0 items-center justify-center rounded border bg-muted/60">
          <Icon className="size-4.5 text-foreground/70" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold">
            {product.name}
          </div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">
            {product.subtitle}
          </div>
        </div>
        {product.kind === "data-connection" ? (
          <StatusPill
            intent={
              product.executionStatus === "active" ? "success" : "warning"
            }
          >
            {product.executionStatus === "active" ? "실행 가능" : "정의만"}
          </StatusPill>
        ) : null}
      </div>
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
      <p className="line-clamp-2 text-xs text-muted-foreground">
        {product.description}
      </p>
    </button>
  );
}
