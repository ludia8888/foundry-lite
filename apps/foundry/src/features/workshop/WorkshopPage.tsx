import type { FoundryLiteApiError, ResourceItem } from "@foundry-lite/sdk";
import {
  createRequestId,
  normalizeFoundryLiteError,
  retryWithBackoff,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteProvidedOntologyWorkspaceShell,
} from "@foundry-lite/sdk/react";
import { Eye, Info, Pencil, Save } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import { BuilderMode } from "./builder/BuilderMode";
import {
  WORKSHOP_APP_RESOURCE_TYPE,
  WORKSHOP_APP_SOURCE_REF,
  WORKSHOP_APP_SOURCE_SURFACE,
  appDefinitionFromResource,
  findWorkshopAppResource,
  isRunnableApp,
  loadAppDefinition,
  resourceMetadataForAppDefinition,
  saveAppDefinition,
  type AppDefinition,
} from "./lib/app-model";
import { RuntimeMode } from "./runtime/RuntimeMode";

type WorkshopMode = "builder" | "runtime";

/**
 * Workshop: 온톨로지 객체·액션을 조합해 최소 업무 앱을 만든다.
 * 빌더 모드(위젯 배치·설정) ↔ 런타임 모드(실데이터 3-pane) 전환.
 * 앱 정의는 Resources API에 저장하고 localStorage는 보조 캐시로만 사용한다.
 */
export default function WorkshopPage() {
  const client = useFoundryLiteClient();
  const [mode, setMode] = useState<WorkshopMode>("builder");
  const [definition, setDefinition] = useState<AppDefinition>(() =>
    loadAppDefinition(),
  );
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(
    () => definition.page.sections[0]?.id ?? null,
  );
  const [savedVersion, setSavedVersion] = useState<number>(
    () => definition.version,
  );
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(
    () => definition.version === 0,
  );
  const [resource, setResource] = useState<ResourceItem | null>(null);
  const [persistenceError, setPersistenceError] =
    useState<FoundryLiteApiError | null>(null);
  const [isLoadingDefinition, setIsLoadingDefinition] = useState(true);
  const [isSavingDefinition, setIsSavingDefinition] = useState(false);
  const hasUserEditedRef = useRef(false);

  const workspace = useFoundryLiteProvidedOntologyWorkspaceShell({
    key: ["workshop", "catalog"],
  });

  const objectViews = workspace.objectViews;
  const actionViews = workspace.actionViews;
  const objectViewsByApiName = workspace.objectViewsByApiName;

  const runnable = useMemo(
    () => isRunnableApp(definition.page),
    [definition.page],
  );
  const isOntologyMissing = Boolean(
    workspace.error &&
      !workspace.catalog &&
      isActiveOntologyMissingError(workspace.error),
  );
  const isDirty =
    hasUnsavedChanges ||
    definition.version === 0 ||
    definition.version !== savedVersion;

  useEffect(() => {
    let isActive = true;
    setIsLoadingDefinition(true);
    void retryWithBackoff(() => client.resources.items.search())
      .then((result) => {
        if (!isActive) return;
        const savedResource = findWorkshopAppResource(result.items);
        setResource(savedResource);
        const savedDefinition = savedResource
          ? appDefinitionFromResource(savedResource)
          : null;
        if (savedDefinition && !hasUserEditedRef.current) {
          setDefinition(savedDefinition);
          setSelectedSectionId(savedDefinition.page.sections[0]?.id ?? null);
          setSavedVersion(savedDefinition.version);
          setHasUnsavedChanges(false);
        }
        setPersistenceError(null);
      })
      .catch((caught) => {
        if (isActive) setPersistenceError(normalizeFoundryLiteError(caught));
      })
      .finally(() => {
        if (isActive) setIsLoadingDefinition(false);
      });
    return () => {
      isActive = false;
    };
  }, [client]);

  const handleDefinitionChange = useCallback((next: AppDefinition) => {
    hasUserEditedRef.current = true;
    setDefinition(next);
    setHasUnsavedChanges(true);
  }, []);

  const handleSave = useCallback(async () => {
    if (isSavingDefinition) return;
    setIsSavingDefinition(true);
    setPersistenceError(null);
    const saved = saveAppDefinition(definition);
    setDefinition(saved);
    hasUserEditedRef.current = false;
    try {
      const updated = await client.resources.items.register(
        {
          resourceType: WORKSHOP_APP_RESOURCE_TYPE,
          displayName: saved.name,
          sourceSurface: WORKSHOP_APP_SOURCE_SURFACE,
          sourceRef: WORKSHOP_APP_SOURCE_REF,
          operationsPath: "/workshop",
          metadata: resourceMetadataForAppDefinition(saved),
        },
        { idempotencyKey: createRequestId("workshop-save") },
      );
      const persisted = appDefinitionFromResource(updated) ?? saved;
      setResource(updated);
      setSavedVersion(persisted.version);
      if (hasUserEditedRef.current) {
        setHasUnsavedChanges(true);
      } else {
        setDefinition(persisted);
        setSelectedSectionId(persisted.page.sections[0]?.id ?? null);
        setHasUnsavedChanges(false);
      }
    } catch (caught) {
      setHasUnsavedChanges(true);
      setPersistenceError(normalizeFoundryLiteError(caught));
    } finally {
      setIsSavingDefinition(false);
    }
  }, [client, definition, isSavingDefinition]);

  const handleEnterRuntime = async () => {
    if (isDirty) await handleSave();
    setMode("runtime");
  };

  if (workspace.isLoading && !workspace.catalog) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="Workshop"
          description="객체 뷰 조합으로 최소 업무 앱 구성"
        />
        <LoadingState rowCount={6} />
      </div>
    );
  }

  if (workspace.error && !workspace.catalog && !isOntologyMissing) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="Workshop"
          description="객체 뷰 조합으로 최소 업무 앱 구성"
        />
        <ErrorState
          error={workspace.error}
          onRetry={() => workspace.reload()}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-[#d5dce1] bg-white px-4">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded bg-[#7548c9] text-[12px] font-bold text-white">
            W
          </span>
          <span className="truncate text-[13px] font-semibold text-[#1c2127]">
            Workshop › {definition.name}
          </span>
          {workspace.versionLabel ? (
            <StatusPill intent="neutral">{workspace.versionLabel}</StatusPill>
          ) : isOntologyMissing ? (
            <StatusPill intent="warning">온톨로지 미연결</StatusPill>
          ) : null}
        </div>

        <div className="ml-4 flex items-center rounded border border-[#d5dce1] p-0.5">
          <button
            type="button"
            onClick={() => setMode("builder")}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded px-3 text-[12px] font-medium",
              mode === "builder"
                ? "bg-[#e8f0fb] text-[#215db0]"
                : "text-[#5f6b7c] hover:bg-muted/50",
            )}
          >
            <Pencil className="size-3.5" />
            빌더
          </button>
          <button
            type="button"
            onClick={() => setMode("runtime")}
            className={cn(
              "flex h-7 items-center gap-1.5 rounded px-3 text-[12px] font-medium",
              mode === "runtime"
                ? "bg-[#e8f0fb] text-[#215db0]"
                : "text-[#5f6b7c] hover:bg-muted/50",
            )}
          >
            <Eye className="size-3.5" />
            런타임
          </button>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <RequestTelemetry />
          {definition.savedAt ? (
            <span className="hidden font-mono text-[11px] text-muted-foreground lg:inline">
              서버 저장 v{definition.version}
            </span>
          ) : null}
          {isLoadingDefinition ? (
            <StatusPill intent="neutral">저장 상태 확인 중</StatusPill>
          ) : null}
          {mode === "builder" ? (
            <>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={!isDirty || isSavingDefinition}
                className="flex h-8 items-center gap-1.5 rounded bg-[#238551] px-3 text-[13px] font-semibold text-white shadow-sm hover:bg-[#1c7048] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Save className="size-3.5" />
                {isSavingDefinition ? "저장 중" : "저장 후 게시"}
              </button>
              <button
                type="button"
                onClick={() => void handleEnterRuntime()}
                className="flex h-8 items-center gap-1.5 rounded border border-[#d5dce1] bg-white px-3 text-[13px] font-medium text-[#404854] hover:bg-muted/40"
              >
                <Eye className="size-3.5" />
                보기
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="flex items-center gap-2 border-b border-[#e4e9ed] bg-[#fbfcfd] px-4 py-1.5">
        <Info className="size-3.5 text-[#8f99a8]" />
        <span className="text-[11px] text-muted-foreground">
          앱 정의는 서버 Resources 카탈로그에 저장됩니다
          {resource ? ` · ${resource.rid}` : ""}. 미구현 위젯은 팔레트에서
          future 배지로 표시됩니다.
        </span>
        {isOntologyMissing ? (
          <Link
            to="/ontology"
            className="text-[11px] font-semibold text-[#215db0] hover:underline"
          >
            객체·액션 바인딩을 위해 Ontology 연결
          </Link>
        ) : null}
        {persistenceError ? (
          <StatusPill intent="danger">저장 동기화 오류</StatusPill>
        ) : null}
        {isOntologyMissing ? (
          <StatusPill intent="warning">
            빌더 편집 가능 · 런타임은 Ontology 필요
          </StatusPill>
        ) : runnable ? (
          <StatusPill intent="success">최소 업무 앱 구성 완료</StatusPill>
        ) : (
          <StatusPill intent="warning">
            테이블·상세·액션 위젯을 모두 배치하세요
          </StatusPill>
        )}
      </div>

      <div className="min-h-0 flex-1">
        {mode === "builder" ? (
          <BuilderMode
            definition={definition}
            objectViews={objectViews}
            actionViews={actionViews}
            selectedSectionId={selectedSectionId}
            onSelectSection={setSelectedSectionId}
            onChange={handleDefinitionChange}
          />
        ) : isOntologyMissing ? (
          <OntologyRequiredState className="h-full" />
        ) : (
          <RuntimeMode
            page={definition.page}
            objectViewsByApiName={objectViewsByApiName}
          />
        )}
      </div>
    </div>
  );
}
