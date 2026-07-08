import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Layers, Plus, Save, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  buildScope,
  OPERATIONS_BY_RESOURCE_TYPE,
  RESOURCE_TYPE_LABEL,
  type OsdkResourceRow,
  type OsdkResourceType,
} from "./developer-model";
import {
  useCatalogResources,
  type CatalogResource,
} from "./use-developer-queries";

interface ResourceScopeEditorProps {
  appId: string;
  resources: OsdkResourceRow[];
  onSaved: () => void;
}

/** 편집 중 draft 리소스: apiName+type 별로 선택된 operation 집합. */
type ResourceDraft = {
  resourceType: OsdkResourceType;
  resourceApiName: string;
  operations: Set<string>;
};

function resourceKey(resourceType: string, apiName: string): string {
  return `${resourceType}:${apiName}`;
}

function toDrafts(resources: OsdkResourceRow[]): Map<string, ResourceDraft> {
  const map = new Map<string, ResourceDraft>();
  for (const resource of resources) {
    const type = resource.resourceType as OsdkResourceType;
    const operations = new Set<string>();
    for (const scope of resource.scopes) {
      const operation = scope.split(":").at(-1);
      if (operation) operations.add(operation);
    }
    map.set(resourceKey(type, resource.resourceApiName), {
      resourceType: type,
      resourceApiName: resource.resourceApiName,
      operations,
    });
  }
  return map;
}

/**
 * 리소스 권한 편집기 (resource permission editor).
 * 온톨로지 객체/링크/액션/함수를 앱에 연결하고 scope operation을 토글한다.
 * developerConsole.osdkApplications.updateResources 로 저장.
 */
export function ResourceScopeEditor({
  appId,
  resources,
  onSaved,
}: ResourceScopeEditorProps) {
  const client = useFoundryLiteClient();
  const catalogQuery = useCatalogResources();
  const [drafts, setDrafts] = useState<Map<string, ResourceDraft>>(() =>
    toDrafts(resources),
  );
  const [addSelection, setAddSelection] = useState<string>("");
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [lastRequestId, setLastRequestId] = useState<string | null>(null);

  const mutation = useFoundryLiteMutation(
    ({ payload, key }: { payload: ResourceDraft[]; key: string }) =>
      client.developerConsole.osdkApplications.updateResources(
        appId,
        {
          resources: payload.map((draft) => ({
            resourceType: draft.resourceType,
            resourceApiName: draft.resourceApiName,
            scopes: [...draft.operations].map((operation) =>
              buildScope(draft.resourceType, draft.resourceApiName, operation),
            ),
          })),
        },
        { idempotencyKey: key },
      ),
    { lockKey: () => `developer:app:resources:${appId}` },
  );

  const savedKeys = useMemo(
    () =>
      new Set(
        resources.map((resource) =>
          resourceKey(resource.resourceType, resource.resourceApiName),
        ),
      ),
    [resources],
  );

  const catalogByKey = useMemo(() => {
    const map = new Map<string, CatalogResource>();
    for (const item of catalogQuery.data ?? []) {
      map.set(resourceKey(item.resourceType, item.resourceApiName), item);
    }
    return map;
  }, [catalogQuery.data]);

  const availableToAdd = useMemo(
    () =>
      (catalogQuery.data ?? []).filter(
        (item) =>
          !drafts.has(resourceKey(item.resourceType, item.resourceApiName)),
      ),
    [catalogQuery.data, drafts],
  );

  const isDirty = useMemo(() => {
    if (drafts.size !== savedKeys.size) return true;
    const savedDrafts = toDrafts(resources);
    for (const [key, draft] of drafts) {
      const saved = savedDrafts.get(key);
      if (!saved) return true;
      if (saved.operations.size !== draft.operations.size) return true;
      for (const operation of draft.operations) {
        if (!saved.operations.has(operation)) return true;
      }
    }
    return false;
  }, [drafts, resources, savedKeys]);

  const toggleOperation = (key: string, operation: string) => {
    setDrafts((prev) => {
      const next = new Map(prev);
      const draft = next.get(key);
      if (!draft) return prev;
      const operations = new Set(draft.operations);
      if (operations.has(operation)) operations.delete(operation);
      else operations.add(operation);
      next.set(key, { ...draft, operations });
      return next;
    });
  };

  const removeResource = (key: string) => {
    setDrafts((prev) => {
      const next = new Map(prev);
      next.delete(key);
      return next;
    });
  };

  const addResource = () => {
    if (!addSelection) return;
    const item = catalogByKey.get(addSelection);
    if (!item) return;
    setDrafts((prev) => {
      const next = new Map(prev);
      const defaultOperation =
        OPERATIONS_BY_RESOURCE_TYPE[item.resourceType][0];
      next.set(addSelection, {
        resourceType: item.resourceType,
        resourceApiName: item.resourceApiName,
        operations: new Set(defaultOperation ? [defaultOperation] : []),
      });
      return next;
    });
    setAddSelection("");
  };

  const handleSave = async () => {
    const payload = [...drafts.values()].filter(
      (draft) => draft.operations.size > 0,
    );
    const key = idempotencyKey("osdk_application_resources", appId);
    setLastKey(key);
    setLastRequestId(null);
    const result = await mutation.execute({ payload, key });
    if (result) {
      setLastRequestId(
        (result.application as { id?: string })?.id
          ? `updated:${(result.application as { id?: string }).id}`
          : null,
      );
      toast.success("리소스 연결이 저장되었습니다");
      onSaved();
    }
  };

  const draftEntries = [...drafts.entries()];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="section-label">리소스 접근 범위</div>
        <div className="flex items-center gap-2">
          <StatusPill intent={isDirty ? "warning" : "neutral"}>
            {isDirty ? "미저장 변경" : "저장됨"}
          </StatusPill>
          <Button
            size="sm"
            onClick={() => void handleSave()}
            disabled={!isDirty || mutation.isRunning}
          >
            <Save className="size-3.5" /> 저장
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        이 앱이 접근할 수 있는 온톨로지 리소스와 scope를 정의합니다. scope는
        <code className="mx-1 font-mono text-[11px]">
          osdk:{"{type}"}:{"{apiName}"}:{"{operation}"}
        </code>
        형식으로 발급됩니다.
      </p>

      {catalogQuery.error ? (
        <ErrorState
          error={catalogQuery.error}
          onRetry={() => void catalogQuery.reload()}
        />
      ) : null}

      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="section-label">리소스 추가</div>
          <Select value={addSelection} onValueChange={setAddSelection}>
            <SelectTrigger size="sm" className="w-full">
              <SelectValue placeholder="온톨로지 리소스 선택" />
            </SelectTrigger>
            <SelectContent>
              {availableToAdd.length === 0 ? (
                <div className="px-2 py-1.5 text-xs text-muted-foreground">
                  추가할 수 있는 리소스가 없습니다.
                </div>
              ) : (
                availableToAdd.map((item) => (
                  <SelectItem
                    key={resourceKey(item.resourceType, item.resourceApiName)}
                    value={resourceKey(item.resourceType, item.resourceApiName)}
                  >
                    <span className="text-muted-foreground">
                      {RESOURCE_TYPE_LABEL[item.resourceType]}
                    </span>
                    <span className="ml-1.5 font-mono text-[11px]">
                      {item.resourceApiName}
                    </span>
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={addResource}
          disabled={!addSelection}
        >
          <Plus className="size-3.5" /> 추가
        </Button>
      </div>

      {catalogQuery.isLoading ? (
        <LoadingState rowCount={3} />
      ) : draftEntries.length === 0 ? (
        <EmptyState
          icon={Layers}
          title="연결된 리소스가 없습니다"
          description="위에서 온톨로지 객체/액션/링크/함수를 추가하고 scope를 지정하세요."
        />
      ) : (
        <div className="overflow-hidden rounded border bg-card">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="border-b">
                <th className="section-label h-8 px-3 text-left">리소스</th>
                <th className="section-label h-8 px-3 text-left">타입</th>
                <th className="section-label h-8 px-3 text-left">scope</th>
                <th className="section-label h-8 w-10 px-3" />
              </tr>
            </thead>
            <tbody>
              {draftEntries.map(([key, draft]) => {
                const operations =
                  OPERATIONS_BY_RESOURCE_TYPE[draft.resourceType] ?? [];
                return (
                  <tr key={key} className="border-b last:border-b-0">
                    <td className="h-9 px-3 align-middle font-mono text-[11px]">
                      {draft.resourceApiName}
                    </td>
                    <td className="h-9 px-3 align-middle">
                      <StatusPill intent="info">
                        {RESOURCE_TYPE_LABEL[draft.resourceType]}
                      </StatusPill>
                    </td>
                    <td className="h-9 px-3 align-middle">
                      <div className="flex flex-wrap gap-3">
                        {operations.map((operation) => {
                          const checkboxId = `${key}:${operation}`;
                          return (
                            <label
                              key={operation}
                              htmlFor={checkboxId}
                              className="flex cursor-pointer items-center gap-1.5"
                            >
                              <Checkbox
                                id={checkboxId}
                                checked={draft.operations.has(operation)}
                                onCheckedChange={() =>
                                  toggleOperation(key, operation)
                                }
                              />
                              <span className="font-mono text-[11px]">
                                {operation}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </td>
                    <td className="h-9 px-3 text-right align-middle">
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => removeResource(key)}
                        aria-label="리소스 제거"
                      >
                        <Trash2 className="text-muted-foreground" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {mutation.error ? <ErrorState error={mutation.error} /> : null}
      {lastKey ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>idempotency_key={lastKey}</span>
          {lastRequestId ? <span>{lastRequestId}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
