import { FileSearch, ShieldCheck } from "lucide-react";
import { forwardRef } from "react";
import { Link } from "react-router";

import { cn } from "@/lib/utils";

import {
  type AgentCitationView,
  asText,
  citationDocumentHref,
  shortHash,
} from "../aip-model";

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
  const evidence = citation.evidence;
  const documentHref = citationDocumentHref(citation);
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

      {evidence ? (
        <div className="mt-2 rounded border border-[#C8CED6] bg-[#F6F7F9] p-2">
          <div className="mb-1 flex items-center justify-between gap-2">
            <div className="section-label">Immutable evidence</div>
            <span className="flex items-center gap-1 text-[9px] text-[#0F6B3E]">
              <ShieldCheck className="size-3" />
              open 시 서버 재검증
            </span>
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            <MetaItem
              label="Media version"
              value={asText(evidence.mediaItemVersionId)}
              mono
            />
            <MetaItem
              label="Content unit"
              value={asText(evidence.contentUnitId)}
              mono
            />
            <MetaItem
              label="Page"
              value={asText(evidence.pageNumber)}
            />
            <MetaItem
              label="Derivative"
              value={asText(evidence.derivativeKind)}
            />
            <MetaItem
              label="Processor"
              value={processorPin(citation)}
              mono
            />
            <MetaItem
              label="Model"
              value={modelPin(citation)}
              mono
            />
          </div>
          {documentHref ? (
            <Link
              to={documentHref}
              className="mt-2 flex h-7 items-center justify-center gap-1.5 rounded-[2px] border border-[#106BA3] bg-[#137CBD] px-2 text-[10px] font-medium text-white hover:bg-[#106BA3] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#137CBD]/40"
            >
              <FileSearch className="size-3.5" />
              원본 근거 열기
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});

function processorPin(citation: AgentCitationView): string {
  const evidence = citation.evidence;
  if (!evidence) return "-";
  const name = evidence.processorName;
  const version = evidence.processorVersion;
  return name && version ? `${name}@${version}` : name ?? version ?? "-";
}

function modelPin(citation: AgentCitationView): string {
  const evidence = citation.evidence;
  if (!evidence) return "-";
  const name = evidence.modelName;
  const version = evidence.modelVersion;
  return name && version ? `${name}@${version}` : name ?? version ?? "-";
}
