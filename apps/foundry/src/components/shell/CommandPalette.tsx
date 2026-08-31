import { FolderTree } from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { SCREEN_GROUP_LABELS, SCREENS, type ScreenGroup } from "@/lib/screens";

import { useShellResources } from "@/components/shell/use-shell-resources";

const GROUP_ORDER: readonly ScreenGroup[] = [
  "data",
  "ontology",
  "apps",
  "operations",
];

function toAppRoute(path: string | null | undefined, fallback: string): string {
  if (!path) return fallback;
  return path.startsWith("/api/") ? path.slice(4) : path;
}

interface CommandPaletteProps {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
}

/** Quicksearch 커맨드 팔레트 (⌘K). 화면 이동과 서버 리소스 검색을 담당한다. */
export function CommandPalette({ isOpen, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { resources, isLoading, error } = useShellResources(isOpen);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Palantir Quicksearch 관례(⌘J)와 ⌘K를 모두 지원한다.
      if (
        (event.key === "k" || event.key === "j") &&
        (event.metaKey || event.ctrlKey)
      ) {
        if (
          event.key === "k" &&
          document.querySelector("[data-workshop-runtime]")
        ) {
          return;
        }
        event.preventDefault();
        onOpenChange(!isOpen);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onOpenChange]);

  return (
    <CommandDialog open={isOpen} onOpenChange={onOpenChange}>
      <CommandInput
        aria-label="리소스 및 화면 검색"
        placeholder="리소스, 화면 이름으로 검색..."
      />
      <CommandList>
        <CommandEmpty>결과가 없습니다.</CommandEmpty>
        <CommandGroup heading="리소스">
          {isLoading && resources.length === 0 ? (
            <CommandItem disabled value="__resources-loading">
              리소스 불러오는 중...
            </CommandItem>
          ) : null}
          {error ? (
            <CommandItem disabled value="__resources-error">
              리소스를 불러오지 못했습니다.
            </CommandItem>
          ) : null}
          {resources.map((resource) => (
            <CommandItem
              key={resource.rid}
              value={`${resource.displayName} ${resource.resourceType} ${resource.rid}`}
              onSelect={() => {
                onOpenChange(false);
                navigate(toAppRoute(resource.operationsPath, "/projects"));
              }}
            >
              <FolderTree className="size-3.5" />
              <span>{resource.displayName}</span>
              <span className="font-mono text-xs text-muted-foreground">
                {resource.resourceType}
              </span>
            </CommandItem>
          ))}
        </CommandGroup>
        {GROUP_ORDER.map((group) => (
          <CommandGroup key={group} heading={SCREEN_GROUP_LABELS[group]}>
            {SCREENS.filter((screen) => screen.group === group).map(
              (screen) => (
                <CommandItem
                  key={screen.id}
                  value={`${screen.title} ${screen.description}`}
                  onSelect={() => {
                    onOpenChange(false);
                    navigate(screen.route);
                  }}
                >
                  <screen.icon className="size-3.5" />
                  <span>{screen.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {screen.description}
                    {screen.isImplemented ? "" : " (예정)"}
                  </span>
                </CommandItem>
              ),
            )}
          </CommandGroup>
        ))}
      </CommandList>
    </CommandDialog>
  );
}
