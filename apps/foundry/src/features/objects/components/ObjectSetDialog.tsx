import type {
  GenericObject,
  ObjectFilter,
  ObjectSetCreateRequest,
  ObjectSetPayload,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteQuery,
  type FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { Layers } from "lucide-react";
import { useCallback, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

import {
  parseObjectFilterToPills,
  type FilterPill,
} from "../lib/explorer-model";

type SetKind = "dynamic" | "static";

interface ObjectSetDialogProps {
  objectView: FoundryLiteOntologyObjectView | null;
  pills: FilterPill[];
  where: ObjectFilter | undefined;
  objects: GenericObject[];
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  onApplyPills: (pills: FilterPill[]) => void;
}

function SavedSetRow({
  set,
  onApplyPills,
}: {
  set: ObjectSetPayload;
  onApplyPills: (pills: FilterPill[]) => void;
}) {
  const isDynamic = set.setType === "dynamic";
  const parsedPills = isDynamic
    ? parseObjectFilterToPills(
        (set.definition as { filter?: unknown }).filter ?? null,
      )
    : null;
  return (
    <div className="flex items-center gap-2 border-b px-3 py-2 text-xs last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{set.name}</div>
        <div className="truncate font-mono text-[10px] text-muted-foreground">
          {set.id} · 객체 {set.objectIds.length}개
        </div>
      </div>
      <StatusPill intent={isDynamic ? "info" : "neutral"}>
        {isDynamic ? "동적" : "정적"}
      </StatusPill>
      {isDynamic ? (
        parsedPills ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => onApplyPills(parsedPills)}
          >
            필터 적용
          </Button>
        ) : (
          <StatusPill intent="warning">복합 필터 · future</StatusPill>
        )
      ) : null}
    </div>
  );
}

/** ObjectSet 저장/재사용 다이얼로그: 현재 필터(동적) 또는 결과 ID(정적) 저장. */
export function ObjectSetDialog({
  objectView,
  pills,
  where,
  objects,
  isOpen,
  onOpenChange,
  onApplyPills,
}: ObjectSetDialogProps) {
  const client = useFoundryLiteClient();
  const apiName = objectView?.apiName ?? null;
  const [name, setName] = useState("");
  const [setKind, setSetKind] = useState<SetKind>("dynamic");

  const loadSets = useCallback(() => {
    return client.objectSets.list(apiName ? { objectType: apiName } : {});
  }, [apiName, client]);
  const listQuery = useFoundryLiteQuery(["object-sets", apiName], loadSets, {
    enabled: isOpen,
  });

  const createMutation = useFoundryLiteMutation(
    (payload: ObjectSetCreateRequest) => client.objectSets.create(payload),
    {
      onSuccess: () => {
        setName("");
        void listQuery.reload();
      },
    },
  );

  const canSaveDynamic = pills.length > 0;
  const canSaveStatic = objects.length > 0;
  const canSubmit =
    apiName !== null &&
    name.trim().length > 0 &&
    (setKind === "dynamic" ? canSaveDynamic : canSaveStatic);

  const handleCreate = () => {
    if (!apiName || !canSubmit) return;
    const payload: ObjectSetCreateRequest =
      setKind === "dynamic"
        ? {
            name: name.trim(),
            objectType: apiName,
            setType: "dynamic",
            filter: (where ?? null) as Record<string, unknown> | null,
          }
        : {
            name: name.trim(),
            objectType: apiName,
            setType: "static",
            ids: objects.map((object) => object.objectId),
          };
    void createMutation.execute(payload);
  };

  const sets = listQuery.data?.items ?? [];

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-1.5 text-sm">
            <Layers className="size-4" />
            ObjectSet
          </DialogTitle>
          <DialogDescription className="text-xs">
            현재 탐색 조건을 저장해 두고 나중에 다시 불러올 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-2 rounded border p-3">
            <div className="section-label">새 ObjectSet 저장</div>
            <Input
              className="h-8 text-xs"
              placeholder="ObjectSet 이름"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <RadioGroup
              value={setKind}
              onValueChange={(next) => setSetKind(next as SetKind)}
              className="gap-1.5"
            >
              <Label className="flex items-start gap-2 text-xs font-normal">
                <RadioGroupItem value="dynamic" disabled={!canSaveDynamic} />
                <span>
                  동적 — 현재 필터 조건 저장 (필터 {pills.length}개)
                  {!canSaveDynamic ? (
                    <span className="block text-[10px] text-muted-foreground">
                      필터 칩이 1개 이상 있어야 저장할 수 있습니다.
                    </span>
                  ) : null}
                </span>
              </Label>
              <Label className="flex items-start gap-2 text-xs font-normal">
                <RadioGroupItem value="static" disabled={!canSaveStatic} />
                <span>
                  정적 — 현재 결과 객체 ID 고정 (객체 {objects.length}개)
                </span>
              </Label>
            </RadioGroup>
            {createMutation.error ? (
              <ErrorState error={createMutation.error} />
            ) : null}
            {createMutation.result ? (
              <div className="space-y-0.5 rounded border border-success/40 bg-success/5 p-2 font-mono text-[11px] text-muted-foreground">
                <div className="flex items-center gap-2 font-sans">
                  <StatusPill intent="success">저장됨</StatusPill>
                  <span>{createMutation.result.name}</span>
                </div>
                <div>set_id={createMutation.result.id}</div>
                {createMutation.requestId ? (
                  <div>request_id={createMutation.requestId}</div>
                ) : null}
              </div>
            ) : null}
            <Button
              size="sm"
              className="w-full"
              disabled={!canSubmit || createMutation.isRunning}
              onClick={handleCreate}
            >
              {createMutation.isRunning ? "저장 중..." : "저장"}
            </Button>
          </div>
          <div className="rounded border">
            <div className="section-label border-b px-3 py-2">
              저장된 ObjectSet
              {objectView ? ` · ${objectView.displayName}` : null}
            </div>
            {listQuery.isLoading ? (
              <LoadingState rowCount={3} className="p-3" />
            ) : listQuery.error ? (
              <ErrorState
                error={listQuery.error}
                onRetry={() => void listQuery.reload()}
                className="m-3"
              />
            ) : sets.length === 0 ? (
              <EmptyState
                title="저장된 ObjectSet이 없습니다"
                description="필터를 구성한 뒤 위에서 저장하면 목록에 나타납니다."
                className="m-3 border-none p-4"
              />
            ) : (
              <div className="max-h-52 overflow-auto">
                {sets.map((set) => (
                  <SavedSetRow
                    key={set.id}
                    set={set}
                    onApplyPills={(nextPills) => {
                      onApplyPills(nextPills);
                      onOpenChange(false);
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
