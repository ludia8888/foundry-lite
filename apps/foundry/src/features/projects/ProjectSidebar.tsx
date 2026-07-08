import type { ResourceProject } from "@foundry-lite/sdk";
import {
  Archive,
  Briefcase,
  ChevronDown,
  ChevronRight,
  FolderClosed,
  FolderPlus,
  Library,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import type { FolderNode } from "./resource-model";

export type WorkspaceNav = "files" | "trash";

interface ProjectSidebarProps {
  projects: ResourceProject[];
  activeProject: ResourceProject | null;
  onSelectProject: (projectId: string) => void;
  onCreateProject: () => void;
  onCreateFolder: () => void;
  tree: FolderNode[];
  selectedFolderId: string | null;
  onSelectFolder: (folderId: string | null) => void;
  onOpenCatalog: () => void;
  activeNav: WorkspaceNav;
  onSelectNav: (nav: WorkspaceNav) => void;
  trashCount: number;
  totalCount: number;
  isLoading: boolean;
}

interface FolderRowProps {
  node: FolderNode;
  depth: number;
  selectedFolderId: string | null;
  onSelectFolder: (folderId: string | null) => void;
}

function FolderRow({
  node,
  depth,
  selectedFolderId,
  onSelectFolder,
}: FolderRowProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  const hasChildren = node.children.length > 0;
  const isSelected = selectedFolderId === node.id;
  const Chevron = isExpanded ? ChevronDown : ChevronRight;

  return (
    <div>
      <div
        className={cn(
          "flex h-7 items-center gap-1 rounded pr-2 text-[12px] hover:bg-muted/60",
          isSelected && "bg-accent font-medium hover:bg-accent",
        )}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-label={isExpanded ? "폴더 접기" : "폴더 펼치기"}
            className="flex size-4 shrink-0 items-center justify-center text-muted-foreground"
            onClick={(event) => {
              event.stopPropagation();
              setIsExpanded((previous) => !previous);
            }}
          >
            <Chevron className="size-3.5" />
          </button>
        ) : (
          <span className="size-4 shrink-0" />
        )}
        <button
          type="button"
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-1 text-left"
          onClick={() => onSelectFolder(node.id)}
        >
          <FolderClosed className="size-3.5 shrink-0 text-warning" />
          <span className="min-w-0 flex-1 truncate">{node.label}</span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {node.count}
          </span>
        </button>
      </div>
      {hasChildren && isExpanded
        ? node.children.map((child) => (
            <FolderRow
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedFolderId={selectedFolderId}
              onSelectFolder={onSelectFolder}
            />
          ))
        : null}
    </div>
  );
}

interface NavItemProps {
  icon: typeof Briefcase;
  label: string;
  isActive?: boolean;
  isFuture?: boolean;
  count?: number;
  onClick?: () => void;
}

function NavItem({
  icon: Icon,
  label,
  isActive,
  isFuture,
  count,
  onClick,
}: NavItemProps) {
  return (
    <button
      type="button"
      disabled={isFuture}
      onClick={onClick}
      className={cn(
        "flex h-7 w-full items-center gap-2 rounded px-2 text-left text-[12px]",
        isActive && "bg-accent font-medium text-primary",
        !isActive && !isFuture && "hover:bg-muted/60",
        isFuture && "cursor-not-allowed text-muted-foreground/70",
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {typeof count === "number" ? (
        <span className="font-mono text-[11px] text-muted-foreground">
          {count}
        </span>
      ) : null}
      {isFuture ? <StatusPill intent="neutral">future</StatusPill> : null}
    </button>
  );
}

/** 좌측 사이드바: 프로젝트 선택/생성 + 워크스페이스 내비 + 서버 폴더 트리. */
export function ProjectSidebar({
  projects,
  activeProject,
  onSelectProject,
  onCreateProject,
  onCreateFolder,
  tree,
  selectedFolderId,
  onSelectFolder,
  onOpenCatalog,
  activeNav,
  onSelectNav,
  trashCount,
  totalCount,
  isLoading,
}: ProjectSidebarProps) {
  return (
    <aside className="relative z-10 flex max-h-96 w-full shrink-0 flex-col overflow-y-auto border-b bg-card lg:max-h-none lg:w-60 lg:border-r lg:border-b-0">
      <div className="border-b p-3">
        <div className="section-label mb-1.5">프로젝트</div>
        {projects.length > 0 ? (
          <Select
            value={activeProject?.id ?? ""}
            onValueChange={onSelectProject}
          >
            <SelectTrigger size="sm" className="h-7 w-full text-[12px]">
              <SelectValue placeholder="프로젝트 선택" />
            </SelectTrigger>
            <SelectContent>
              {projects.map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  {project.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <div className="rounded border border-dashed px-2 py-1.5 text-[11px] text-muted-foreground">
            프로젝트가 없습니다. 새 프로젝트를 만들거나 카탈로그 동기화를
            실행하세요.
          </div>
        )}
        {activeProject ? (
          <div className="mt-1.5 truncate font-mono text-[11px] text-muted-foreground">
            {activeProject.rid}
          </div>
        ) : null}
        <div className="mt-1.5">
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[11px]"
            onClick={onCreateProject}
          >
            <Plus className="size-3" />새 프로젝트
          </Button>
        </div>
      </div>

      <div className="border-b p-3">
        <div className="section-label">프로젝트 워크스페이스</div>
        <div className="mb-1.5 text-[11px] text-muted-foreground">
          멤버 전용
        </div>
        <nav className="space-y-0.5">
          <NavItem
            icon={Briefcase}
            label="파일"
            isActive={activeNav === "files"}
            count={totalCount}
            onClick={() => onSelectNav("files")}
          />
          <NavItem icon={Sparkles} label="자동 저장" isFuture />
          <NavItem
            icon={Library}
            label="프로젝트 카탈로그"
            count={totalCount}
            onClick={onOpenCatalog}
          />
          <NavItem
            icon={Trash2}
            label="휴지통"
            isActive={activeNav === "trash"}
            count={trashCount}
            onClick={() => onSelectNav("trash")}
          />
        </nav>
      </div>

      <div className="flex-1 p-3">
        <div className="mb-1.5 flex items-center justify-between">
          <div className="section-label">폴더</div>
          <Button
            size="sm"
            variant="ghost"
            className="h-5 px-1.5 text-[11px]"
            disabled={activeProject === null}
            onClick={onCreateFolder}
          >
            <FolderPlus className="size-3" />새 폴더
          </Button>
        </div>
        {isLoading ? (
          <LoadingState rowCount={6} />
        ) : (
          <div className="space-y-0.5">
            <button
              type="button"
              className={cn(
                "flex h-7 w-full cursor-pointer items-center gap-1.5 rounded px-2 text-left text-[12px] hover:bg-muted/60",
                selectedFolderId === null &&
                  "bg-accent font-medium hover:bg-accent",
              )}
              onClick={() => onSelectFolder(null)}
            >
              <Archive className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="flex-1">전체 리소스</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {totalCount}
              </span>
            </button>
            {tree.map((node) => (
              <FolderRow
                key={node.id}
                node={node}
                depth={0}
                selectedFolderId={selectedFolderId}
                onSelectFolder={onSelectFolder}
              />
            ))}
          </div>
        )}
        {!isLoading && tree.length === 0 && activeProject !== null ? (
          <div className="mt-3 text-[11px] text-muted-foreground">
            폴더가 없습니다. 새 폴더 버튼으로 만들 수 있습니다.
          </div>
        ) : null}
      </div>
    </aside>
  );
}
