import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { FolderKanban } from "lucide-react";
import { useCallback, useEffect } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { WizardField, WizardStepFooter } from "./WizardFields";

/** resources.projects.list — 소스를 저장할 프로젝트 카탈로그. */
function useResourceProjects() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.resources.projects.list(), [client]);
  return useFoundryLiteQuery(["data-connection", "wizard", "projects"], load);
}

interface SaveToProjectStepProps {
  templateDisplayName: string;
  displayName: string;
  onDisplayNameChange: (value: string) => void;
  projectId: string | null;
  onProjectIdChange: (projectId: string | null) => void;
  onProjectNameChange: (projectName: string | null) => void;
  onContinue: () => void;
}

/**
 * 3단계 프로젝트에 저장: 소스 이름 지정 + 저장할 프로젝트 선택
 * (Palantir set-up-source "프로젝트에 소스 저장하기" 구조).
 */
export function SaveToProjectStep({
  templateDisplayName,
  displayName,
  onDisplayNameChange,
  projectId,
  onProjectIdChange,
  onProjectNameChange,
  onContinue,
}: SaveToProjectStepProps) {
  const projectsQuery = useResourceProjects();
  const projects = projectsQuery.data ?? [];
  const hasLoadedProjects = !projectsQuery.isLoading || projectsQuery.data;
  const canUseDefaultLocation =
    hasLoadedProjects && !projectsQuery.error && projects.length === 0;

  useEffect(() => {
    if (projectId === null && projects.length > 0) {
      onProjectIdChange(projects[0].id);
      onProjectNameChange(projects[0].displayName);
    }
  }, [projectId, projects, onProjectIdChange, onProjectNameChange]);

  const canContinue =
    displayName.trim().length > 0 &&
    (projectId !== null || canUseDefaultLocation);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">프로젝트에 소스 저장</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          소스의 이름을 지정하고 저장할 프로젝트를 선택하세요. 일반적으로
          소스마다 새 프로젝트를 사용하면 파생 데이터셋 권한을 가장 자연스럽게
          설정할 수 있습니다.
        </p>
      </div>
      <WizardField
        label="소스 이름"
        helper={`${templateDisplayName} 소스의 표시 이름입니다.`}
      >
        <Input
          value={displayName}
          onChange={(event) => onDisplayNameChange(event.target.value)}
          placeholder="예: 주문 ERP 연결"
          className="h-8 text-xs"
        />
      </WizardField>
      <WizardField
        label="프로젝트"
        helper="소스가 표시·카탈로그로 등록될 프로젝트입니다. 소스 리소스 자체는 다음 단계에서 생성됩니다."
      >
        {projectsQuery.error ? (
          <ErrorState
            error={projectsQuery.error}
            onRetry={() => void projectsQuery.reload()}
          />
        ) : (
          <Select
            value={projectId ?? ""}
            onValueChange={(value) => {
              onProjectIdChange(value || null);
              onProjectNameChange(
                projects.find((project) => project.id === value)?.displayName ??
                  null,
              );
            }}
          >
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue
                placeholder={
                  projectsQuery.isLoading
                    ? "프로젝트 불러오는 중…"
                    : "프로젝트 선택"
                }
              />
            </SelectTrigger>
            <SelectContent>
              {projects.map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  <FolderKanban className="size-3.5 text-muted-foreground" />
                  {project.displayName}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {canUseDefaultLocation ? (
          <div className="rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            등록된 프로젝트가 없어 이 소스는 tenant 기본 위치에서 먼저
            생성됩니다. 프로젝트가 생기면 Files 화면에서 리소스를 정리할 수
            있습니다.
          </div>
        ) : null}
      </WizardField>
      <WizardStepFooter
        right={
          <Button size="sm" disabled={!canContinue} onClick={onContinue}>
            소스 생성 및 계속
          </Button>
        }
      />
    </div>
  );
}
