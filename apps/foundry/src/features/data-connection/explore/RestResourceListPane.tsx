import type { ConnectorResource } from "@foundry-lite/sdk";
import { Database, RefreshCw, Search, Table2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface RestResourceListPaneProps {
  resources: readonly ConnectorResource[];
  resourceCount: number;
  searchText: string;
  selectedResourceName: string;
  onSearchTextChange: (value: string) => void;
  onSelectResource: (resourceName: string) => void;
  onReload: () => void;
}

export function RestResourceListPane({
  resources,
  resourceCount,
  searchText,
  selectedResourceName,
  onSearchTextChange,
  onSelectResource,
  onReload,
}: RestResourceListPaneProps) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r">
      <div className="flex h-10 items-center justify-between border-b px-3">
        <div className="flex items-center gap-2">
          <Database className="size-3.5 text-primary" />
          <span className="text-xs font-semibold">리소스</span>
          <span className="text-[10px] text-muted-foreground">
            {resourceCount}
          </span>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="리소스 새로고침"
          onClick={onReload}
        >
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      <div className="border-b p-2">
        <div className="relative">
          <Search className="absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchText}
            onChange={(event) => onSearchTextChange(event.target.value)}
            placeholder="리소스 검색"
            aria-label="리소스 검색"
            className="h-7 pl-7 text-xs"
          />
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {resources.length === 0 ? (
          <div className="px-2 py-6 text-center text-[11px] text-muted-foreground">
            검색 결과가 없습니다.
          </div>
        ) : (
          resources.map((resource) => (
            <button
              key={resource.resourceName}
              type="button"
              data-testid={`rest-resource-${resource.resourceName}`}
              onClick={() => onSelectResource(resource.resourceName)}
              className={cn(
                "mb-0.5 flex w-full items-start gap-2 rounded px-2 py-2 text-left",
                selectedResourceName === resource.resourceName
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-muted/60",
              )}
            >
              <Table2 className="mt-0.5 size-3.5 shrink-0 text-primary" />
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium">
                  {resource.resourceName}
                </span>
                <span className="block truncate font-mono text-[10px] text-muted-foreground">
                  {resource.resourcePath}
                </span>
              </span>
            </button>
          ))
        )}
      </div>
    </aside>
  );
}
