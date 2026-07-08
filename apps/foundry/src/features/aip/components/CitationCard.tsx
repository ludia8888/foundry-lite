import { forwardRef } from "react";

import { cn } from "@/lib/utils";

import { type AgentCitationView, asText, shortHash } from "../aip-model";

function MetaItem({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-[10px] tracking-[0.3px] text-muted-foreground uppercase">
        {label}
      </span>
      <span
        className={cn(
          "min-w-0 truncate text-[11px] text-foreground/90",
          mono && "font-mono",
        )}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

/** 답변 내 근거 anchor와 연결되는 citation 카드. */
export const CitationCard = forwardRef<
  HTMLDivElement,
  { citation: AgentCitationView; isSelected: boolean }
>(function CitationCard({ citation, isSelected }, ref) {
  const source = citation.source;
  return (
    <div
      ref={ref}
      tabIndex={-1}
      data-citation-order={citation.order}
      className={cn(
        "scroll-mt-2 rounded border bg-card p-2.5 transition-colors outline-none",
        isSelected
          ? "border-primary/60 bg-primary/5 ring-1 ring-primary/30"
          : "border-border",
      )}
    >
      <div className="flex items-start gap-2">
        <span className="mt-px flex size-4 shrink-0 items-center justify-center rounded-sm bg-primary/10 font-mono text-[10px] font-semibold text-primary">
          {citation.order}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12px] font-medium text-foreground">
            {citation.displayLabel ??
              citation.sourceResourceId ??
              citation.contextId ??
              "Citation"}
          </div>
          <div className="mt-1 grid grid-cols-1 gap-y-0.5">
            <MetaItem label="Context" value={asText(citation.contextId)} mono />
            <MetaItem
              label="Source"
              value={asText(citation.sourceResourceId)}
              mono
            />
            <MetaItem
              label="Hash"
              value={shortHash(citation.contentHash)}
              mono
            />
            {citation.renderedRef ? (
              <MetaItem label="Ref" value={citation.renderedRef} />
            ) : null}
          </div>
        </div>
      </div>

      {source ? (
        <div className="mt-2 rounded border border-dashed border-border bg-muted/40 p-2">
          <div className="section-label mb-1">Source preview</div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <MetaItem label="Kind" value={asText(source.kind)} />
            <MetaItem label="Method" value={asText(source.retrievalMethod)} />
            <MetaItem label="Version" value={asText(source.sourceVersion)} />
            <MetaItem label="Tokens" value={asText(source.tokenEstimate)} />
            <MetaItem
              label="Partition"
              value={asText(source.securityPartition)}
              mono
            />
            <MetaItem label="Selected" value={source.selected ? "yes" : "no"} />
            <MetaItem label="Item" value={asText(source.contextItemId)} mono />
            <MetaItem label="Hash" value={shortHash(source.contentHash)} mono />
          </div>
        </div>
      ) : null}
    </div>
  );
});
