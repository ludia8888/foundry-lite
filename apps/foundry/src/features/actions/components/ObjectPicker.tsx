import type { GenericObject } from "@foundry-lite/sdk";

import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

interface ObjectPickerProps {
  objects: GenericObject[];
  targetObjectApiName: string;
  selectedObjectId: string | null;
  onSelect: (objectId: string) => void;
  titleProperty?: string;
  statusProperty?: string;
}

function statusIntent(status: unknown): StatusIntent {
  if (status === "APPROVED") return "success";
  if (status === "REVIEW") return "warning";
  if (status === "PENDING") return "info";
  return "neutral";
}

/** 대상 객체 선택: 액션 target 객체 타입의 인스턴스를 상태/버전과 함께 골라 폼에 연결. */
export function ObjectPicker({
  objects,
  targetObjectApiName,
  selectedObjectId,
  onSelect,
  titleProperty,
  statusProperty = "status",
}: ObjectPickerProps) {
  return (
    <div className="space-y-1.5">
      <div className="section-label px-1">
        대상 객체 · {targetObjectApiName} ({objects.length})
      </div>
      <div className="max-h-56 space-y-1 overflow-auto">
        {objects.length === 0 ? (
          <div className="rounded border border-dashed px-2 py-3 text-center text-[11px] text-muted-foreground">
            대상 객체가 없습니다.
          </div>
        ) : (
          objects.map((object) => {
            const isSelected = object.objectId === selectedObjectId;
            const status = object.properties?.[statusProperty];
            const title = titleProperty
              ? object.properties?.[titleProperty]
              : undefined;
            return (
              <button
                key={object.objectId}
                type="button"
                onClick={() => onSelect(object.objectId)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded border px-2 py-1.5 text-left transition-colors",
                  isSelected
                    ? "border-primary/40 bg-accent"
                    : "border-transparent hover:bg-muted/60",
                )}
              >
                <div className="min-w-0">
                  <div className="truncate font-mono text-[11px] font-medium">
                    {object.objectId}
                  </div>
                  {title !== undefined ? (
                    <div className="truncate text-[10px] text-muted-foreground">
                      {String(title)}
                    </div>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {status !== undefined && status !== null ? (
                    <StatusPill intent={statusIntent(status)}>
                      {String(status)}
                    </StatusPill>
                  ) : null}
                  <span className="font-mono text-[10px] text-muted-foreground">
                    v{object.objectVersion}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
