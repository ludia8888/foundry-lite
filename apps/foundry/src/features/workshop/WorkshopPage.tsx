import type { FoundryLiteApiError } from "@foundry-lite/sdk";
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
import { Link, useParams } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import {
  OntologyRequiredState,
  isActiveOntologyMissingError,
} from "@/components/shared/OntologyRequiredState";
import { PageHeader } from "@/components/shared/PageHeader";
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
  const { applicationId } = useParams();
  const sourceRef = applicationId ?? WORKSHOP_APP_SOURCE_REF;
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
        const savedResource = findWorkshopAppResource(result.items, sourceRef);
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
  }, [client, sourceRef]);

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
          sourceRef,
          operationsPath: applicationId ? `/workshop/${applicationId}` : "/workshop",
          metadata: resourceMetadataForAppDefinition(saved),
        },
        { idempotencyKey: createRequestId("workshop-save") },
      );
      const persisted = appDefinitionFromResource(updated) ?? saved;
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
  }, [applicationId, client, definition, isSavingDefinition, sourceRef]);

  const handleEnterRuntime = async () => {
    if (isDirty) await handleSave();
    setMode("runtime");
  };

  if (workspace.isLoading && !workspace.catalog) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="업무 앱 만들기"
          description="회사 업무에 맞는 화면을 준비하고 있습니다"
        />
        <LoadingState rowCount={6} />
      </div>
    );
  }

  if (workspace.error && !workspace.catalog && !isOntologyMissing) {
    return (
      <div className="space-y-4 p-4">
        <PageHeader
          title="업무 앱 만들기"
          description="회사 업무 화면을 불러오지 못했습니다"
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
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[#e1e6eb] bg-white px-3 py-2 sm:h-16 sm:flex-nowrap sm:gap-3 sm:px-5 sm:py-0">
        <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
          <span className="flex size-9 items-center justify-center rounded-xl bg-[#6651c7] text-[13px] font-black text-white">
            W
          </span>
          <span className="truncate text-[14px] font-bold text-[#1c2127]">
            {definition.name}
          </span>
          {workspace.versionLabel ? (
            <StatusPill intent="success">업무 데이터 연결됨</StatusPill>
          ) : isOntologyMissing ? (
            <StatusPill intent="warning">업무 데이터 미연결</StatusPill>
          ) : null}
        </div>

        <div className="order-3 grid w-full grid-cols-2 items-center rounded-lg border border-[#d5dce1] p-0.5 sm:order-none sm:ml-4 sm:flex sm:w-auto">
          <button
            type="button"
            onClick={() => setMode("builder")}
            className={cn(
              "flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-[12px] font-medium sm:h-7 sm:px-3",
              mode === "builder"
                ? "bg-[#e8f0fb] text-[#215db0]"
                : "text-[#5f6b7c] hover:bg-muted/50",
            )}
          >
            <Pencil className="size-3.5" />
            AI FDE 검토
          </button>
          <button
            type="button"
            onClick={() => setMode("runtime")}
            className={cn(
              "flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md px-2 text-[12px] font-medium sm:h-7 sm:px-3",
              mode === "runtime"
                ? "bg-[#e8f0fb] text-[#215db0]"
                : "text-[#5f6b7c] hover:bg-muted/50",
            )}
          >
            <Eye className="size-3.5" />
            사용자 미리보기
          </button>
        </div>

        <div className="order-4 flex w-full items-center gap-2 sm:order-none sm:ml-auto sm:w-auto">
          {definition.savedAt ? (
            <span className="hidden text-[11px] font-medium text-muted-foreground lg:inline">
              변경사항 게시됨
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
                className="flex h-8 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded bg-[#238551] px-3 text-[13px] font-semibold text-white shadow-sm hover:bg-[#1c7048] disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none"
              >
                <Save className="size-3.5" />
                {isSavingDefinition ? "게시 중" : "변경사항 게시"}
              </button>
              <button
                type="button"
                onClick={() => void handleEnterRuntime()}
                className="flex h-8 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded border border-[#d5dce1] bg-white px-3 text-[13px] font-medium text-[#404854] hover:bg-muted/40 sm:flex-none"
              >
                <Eye className="size-3.5" />
                사용자 화면 보기
              </button>
            </>
          ) : null}
        </div>
      </div>

      <div className="flex min-h-9 flex-wrap items-center gap-2 border-b border-[#e4e9ed] bg-[#f8fafb] px-3 py-2 sm:px-5">
        <Info className="size-3.5 text-[#8f99a8]" />
        <span className="hidden min-w-0 flex-1 text-[11px] text-muted-foreground md:inline">
          여기서 확인한 화면과 업무 규칙은 GPT 안의 화면과 외부 앱에 동일하게 적용됩니다.
        </span>
        {isOntologyMissing ? (
          <Link
            to="/ontology"
            className="text-[11px] font-semibold text-[#215db0] hover:underline"
          >
            업무 데이터와 실행 규칙 연결하기
          </Link>
        ) : null}
        {persistenceError ? (
          <StatusPill intent="danger">저장 동기화 오류</StatusPill>
        ) : null}
        {isOntologyMissing ? (
          <StatusPill intent="warning">
            화면 검토 가능 · 실제 업무 사용 전 데이터 연결 필요
          </StatusPill>
        ) : runnable ? (
          <StatusPill intent="success">사용자 화면 준비 완료</StatusPill>
        ) : (
          <StatusPill intent="warning">
            목록·상세·다음 업무 화면을 확인해 주세요
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
            definition={definition}
            objectViewsByApiName={objectViewsByApiName}
            actionViews={actionViews}
          />
        )}
      </div>
    </div>
  );
}
