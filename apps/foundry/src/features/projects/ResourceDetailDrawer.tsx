import type { ProjectFolder, ResourceProject } from "@foundry-lite/sdk";
import {
  Copy,
  ExternalLink,
  FolderInput,
  RotateCcw,
  Star,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { DatasetDetailSection } from "./DatasetDetailSection";
import { OntologyInsightSection } from "./OntologyInsightSection";
import { PermissionPanel } from "./PermissionPanel";
import { ResourceKindIcon, resourceStatusIntent } from "./ResourceTable";
import type { ResourceRow } from "./resource-model";
import { RESOURCE_KIND_META, formatTimestamp } from "./resource-model";

const ROOT_FOLDER_VALUE = "__root__";

interface ResourceDetailDrawerProps {
  row: ResourceRow;
  project: ResourceProject | null;
  folders: ProjectFolder[];
  isPending: boolean;
  onToggleFavorite: (rid: string) => void;
  onMoveToTrash: (rid: string) => void;
  onRestore: (rid: string) => void;
  onMoveToFolder: (rid: string, folderId: string | null) => void;
  onClose: () => void;
}

function MetaRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-2 text-[12px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

function DetailMetaSection({ row }: { row: ResourceRow }) {
  const meta = RESOURCE_KIND_META[row.kind];
  const handleCopyRid = async () => {
    await navigator.clipboard.writeText(row.rid);
    toast.success("RID를 클립보드에 복사했습니다.");
  };

  return (
    <section className="space-y-1.5">
      <div className="section-label">기본 정보</div>
      <div className="flex items-center gap-1 rounded border bg-muted/40 px-2 py-1">
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
          {row.rid}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="size-6 p-0"
          aria-label="RID 복사"
          onClick={handleCopyRid}
        >
          <Copy className="size-3" />
        </Button>
      </div>
      <MetaRow label="타입">
        <StatusPill intent="neutral">{meta.label}</StatusPill>
      </MetaRow>
      <MetaRow label="소스 surface">{meta.sourceSurface}</MetaRow>
      <MetaRow label="위치">
        <span className="font-mono text-[11px]">
          {row.origin === "catalog" ? row.folderLabel : "카탈로그 미등록"}
        </span>
      </MetaRow>
      <MetaRow label="상태">
        {row.status ? (
          <StatusPill intent={resourceStatusIntent(row.status)}>
            {row.status}
          </StatusPill>
        ) : (
          "—"
        )}
      </MetaRow>
      <MetaRow label="업데이트">
        <span className="font-mono text-[11px]">
          {formatTimestamp(row.updatedAt)}
        </span>
      </MetaRow>
      {row.sourceRef ? (
        <MetaRow label="소스 ref">
          <span className="break-all font-mono text-[11px]">
            {row.sourceRef}
          </span>
        </MetaRow>
      ) : null}
      {row.operationsPath ? (
        <MetaRow label="운영 경로">
          <span className="break-all font-mono text-[11px]">
            {row.operationsPath}
          </span>
        </MetaRow>
      ) : null}
      {row.description ? (
        <p className="rounded bg-muted/40 p-2 text-[11px] text-muted-foreground">
          {row.description}
        </p>
      ) : null}
    </section>
  );
}

function DrawerActions({
  row,
  isPending,
  onToggleFavorite,
  onMoveToTrash,
  onRestore,
}: Pick<
  ResourceDetailDrawerProps,
  "row" | "isPending" | "onToggleFavorite" | "onMoveToTrash" | "onRestore"
>) {
  const navigate = useNavigate();
  const meta = RESOURCE_KIND_META[row.kind];
  const isSurfaceOnly = row.origin === "surface";

  return (
    <section className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[12px]"
          disabled={isSurfaceOnly || isPending}
          onClick={() => onToggleFavorite(row.rid)}
        >
          <Star
            className={cn(
              "size-3.5",
              row.isFavorite && "fill-warning text-warning",
            )}
          />
          {row.isFavorite ? "즐겨찾기 해제" : "즐겨찾기"}
        </Button>
        {row.isTrashed ? (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[12px]"
            disabled={isPending}
            onClick={() => onRestore(row.rid)}
          >
            <RotateCcw className="size-3.5" />
            복원
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-[12px] text-destructive"
            disabled={isSurfaceOnly || isPending}
            onClick={() => onMoveToTrash(row.rid)}
          >
            <Trash2 className="size-3.5" />
            휴지통으로
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[12px]"
          onClick={() => navigate(meta.surfaceRoute)}
        >
          <ExternalLink className="size-3.5" />
          {meta.sourceSurface} 열기
        </Button>
      </div>
      {isSurfaceOnly ? (
        <p className="text-[11px] text-muted-foreground">
          카탈로그 미등록 리소스입니다. 카탈로그 동기화 후 즐겨찾기·휴지통·폴더
          이동을 사용할 수 있습니다.
        </p>
      ) : null}
    </section>
  );
}

function FolderMoveSection({
  row,
  folders,
  isPending,
  onMoveToFolder,
}: Pick<
  ResourceDetailDrawerProps,
  "row" | "folders" | "isPending" | "onMoveToFolder"
>) {
  const currentValue = row.folderId ?? ROOT_FOLDER_VALUE;
  const [targetValue, setTargetValue] = useState(currentValue);

  useEffect(() => {
    setTargetValue(row.folderId ?? ROOT_FOLDER_VALUE);
  }, [row.rid, row.folderId]);

  const activeFolders = folders.filter((folder) => folder.status === "active");
  const isUnchanged = targetValue === currentValue;

  return (
    <section className="space-y-1.5">
      <div className="section-label">폴더로 이동</div>
      <div className="flex items-center gap-1.5">
        <Select value={targetValue} onValueChange={setTargetValue}>
          <SelectTrigger size="sm" className="h-7 min-w-0 flex-1 text-[12px]">
            <SelectValue placeholder="폴더 선택" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ROOT_FOLDER_VALUE}>최상위</SelectItem>
            {activeFolders.map((folder) => (
              <SelectItem key={folder.id} value={folder.id}>
                {folder.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-[12px]"
          disabled={isUnchanged || isPending}
          onClick={() =>
            onMoveToFolder(
              row.rid,
              targetValue === ROOT_FOLDER_VALUE ? null : targetValue,
            )
          }
        >
          <FolderInput className="size-3.5" />
          이동
        </Button>
      </div>
      {activeFolders.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          이동할 폴더가 없습니다. 사이드바에서 새 폴더를 만들 수 있습니다.
        </p>
      ) : null}
    </section>
  );
}

/** 우측 리소스 상세 drawer: 상세/접근 탭 + 서버 연동 액션 + 타입별 evidence 섹션. */
export function ResourceDetailDrawer(props: ResourceDetailDrawerProps) {
  const { row, project, folders, isPending, onMoveToFolder, onClose } = props;

  return (
    <aside className="flex max-h-[32rem] w-full shrink-0 flex-col overflow-y-auto border-t bg-card lg:max-h-none lg:w-80 lg:border-t-0 lg:border-l">
      <div className="flex items-center gap-2 border-b p-3">
        <ResourceKindIcon kind={row.kind} className="size-4" />
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">
          {row.name}
        </span>
        {row.isTrashed ? <StatusPill intent="danger">휴지통</StatusPill> : null}
        <Button
          size="sm"
          variant="ghost"
          className="size-6 p-0"
          aria-label="상세 닫기"
          onClick={onClose}
        >
          <X className="size-3.5" />
        </Button>
      </div>

      <Tabs defaultValue="details" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-3 mt-2 h-7">
          <TabsTrigger value="details" className="h-6 text-[12px]">
            세부 정보
          </TabsTrigger>
          <TabsTrigger value="access" className="h-6 text-[12px]">
            역할
          </TabsTrigger>
        </TabsList>
        <TabsContent value="details" className="space-y-3 p-3">
          <DetailMetaSection row={row} />
          <Separator />
          <DrawerActions
            row={row}
            isPending={isPending}
            onToggleFavorite={props.onToggleFavorite}
            onMoveToTrash={props.onMoveToTrash}
            onRestore={props.onRestore}
          />
          {row.origin === "catalog" && !row.isTrashed ? (
            <>
              <Separator />
              <FolderMoveSection
                row={row}
                folders={folders}
                isPending={isPending}
                onMoveToFolder={onMoveToFolder}
              />
            </>
          ) : null}
          {row.kind === "dataset" && row.datasetNamespace && row.datasetName ? (
            <>
              <Separator />
              <DatasetDetailSection
                namespace={row.datasetNamespace}
                name={row.datasetName}
              />
            </>
          ) : null}
          {row.ontologyResourceType && row.ontologyApiName ? (
            <>
              <Separator />
              <OntologyInsightSection
                resourceType={row.ontologyResourceType}
                apiName={row.ontologyApiName}
              />
            </>
          ) : null}
        </TabsContent>
        <TabsContent value="access" className="p-3">
          <PermissionPanel project={project} />
        </TabsContent>
      </Tabs>
    </aside>
  );
}
