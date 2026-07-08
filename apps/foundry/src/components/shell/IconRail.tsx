import { Home, Menu, Search } from "lucide-react";
import { NavLink, useLocation } from "react-router";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { SCREENS } from "@/lib/screens";
import { cn } from "@/lib/utils";

interface IconRailProps {
  onToggleSidebar: () => void;
  onOpenSearch: () => void;
}

function RailButton({
  label,
  isActive,
  children,
}: {
  label: string;
  isActive?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "flex size-9 items-center justify-center rounded text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
            isActive && "bg-sidebar-accent text-sidebar-foreground",
          )}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

/** 좌측 끝 고정 다크 아이콘 레일 — Palantir 전 앱 공통 글로벌 네비게이션. */
export function IconRail({ onToggleSidebar, onOpenSearch }: IconRailProps) {
  const { pathname } = useLocation();
  const pinnedScreens = SCREENS.filter((screen) => screen.isPinnedToRail);

  return (
    <nav className="flex w-12 shrink-0 flex-col items-center gap-1 bg-rail py-2">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="네비게이션 열기"
        className="mb-1"
      >
        <RailButton label="네비게이션">
          <Menu className="size-4" />
        </RailButton>
      </button>
      <NavLink to="/" aria-label="홈">
        <RailButton label="홈" isActive={pathname === "/"}>
          <Home className="size-4" />
        </RailButton>
      </NavLink>
      <button type="button" onClick={onOpenSearch} aria-label="검색 (⌘K)">
        <RailButton label="검색 (⌘K)">
          <Search className="size-4" />
        </RailButton>
      </button>
      <div className="my-1 h-px w-6 bg-sidebar-border" />
      <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto">
        {pinnedScreens.map((screen) => (
          <NavLink key={screen.id} to={screen.route} aria-label={screen.title}>
            <RailButton
              label={
                screen.isImplemented ? screen.title : `${screen.title} (예정)`
              }
              isActive={
                pathname === screen.route ||
                pathname.startsWith(`${screen.route}/`)
              }
            >
              <screen.icon className="size-4" />
            </RailButton>
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
