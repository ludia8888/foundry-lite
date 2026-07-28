import type { ResourceItem } from "@foundry-lite/sdk";
import {
  Clock,
  FolderClosed,
  Gift,
  Grid3x3,
  HelpCircle,
  Home,
  Search,
  Sparkles,
  Star,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { DEMO_CONTEXT } from "@/lib/api";
import { SCREENS, type ScreenDef } from "@/lib/screens";
import { cn } from "@/lib/utils";

import { getRecentVisits } from "@/components/shell/recent-visits";
import { NotificationsButton } from "@/components/shell/NotificationsButton";
import { useShellResources } from "@/components/shell/use-shell-resources";

const MAX_SIDEBAR_FAVORITES = 6;
const MAX_SIDEBAR_RECENTS = 5;

/** APPLICATIONS 타일 색 (Palantir 사이드바의 컬러 사각 아이콘 관례). */
const APP_TILE_COLORS: Record<string, string> = {
  "data-connection": "bg-[#C87619]",
  "document-intelligence": "bg-[#137CBD]",
  pipelines: "bg-[#0F9960]",
  ontology: "bg-[#7961DB]",
  objects: "bg-[#2D72D2]",
  lineage: "bg-[#00847A]",
  datasets: "bg-[#5F6B7C]",
  projects: "bg-[#2965CC]",
};

interface NavSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSearch: () => void;
}

function SidebarRow({
  icon,
  label,
  hint,
  isActive,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  hint?: string;
  isActive?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-[13px] text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-foreground",
        isActive && "bg-sidebar-accent font-medium text-sidebar-foreground",
      )}
    >
      <span className="flex size-4 shrink-0 items-center justify-center">
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate text-left">{label}</span>
      {hint ? (
        <span className="shrink-0 font-mono text-[10px] text-sidebar-foreground/45">
          {hint}
        </span>
      ) : null}
    </button>
  );
}

function AppRow({
  screen,
  onNavigate,
}: {
  screen: ScreenDef;
  onNavigate: (route: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onNavigate(screen.route)}
      className="flex w-full items-center gap-2.5 rounded px-2 py-1.5 hover:bg-sidebar-accent"
    >
      <span
        className={cn(
          "flex size-5 shrink-0 items-center justify-center rounded-[3px]",
          APP_TILE_COLORS[screen.id] ?? "bg-sidebar-primary",
        )}
      >
        <screen.icon className="size-3 text-white" />
      </span>
      <span className="min-w-0 flex-1 text-left">
        <span className="block truncate text-[13px] text-sidebar-foreground/90">
          {screen.title}
        </span>
        {!screen.isImplemented ? (
          <span className="block text-[10px] italic text-sidebar-foreground/45">
            예정
          </span>
        ) : null}
      </span>
    </button>
  );
}

function SectionLabel({
  label,
  action,
}: {
  label: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-2 pb-1">
      <span className="text-[10px] font-semibold tracking-[0.6px] text-sidebar-foreground/50 uppercase">
        {label}
      </span>
      {action}
    </div>
  );
}

/**
 * Palantir 사이드바 구조 그대로:
 * Home/Search/Notifications/What's New → Recent/Files/Applications
 * → APPLICATIONS (컬러 타일 + View all) → FILES (즐겨찾기) → AIP Assist/Support/Account.
 */
export function NavSidebar({ isOpen, onClose, onOpenSearch }: NavSidebarProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { favorites, isLoading, error, toggleFavorite } =
    useShellResources(isOpen);
  const [isRecentOpen, setIsRecentOpen] = useState(false);
  const recentVisits = isOpen ? getRecentVisits() : [];

  if (!isOpen) {
    return null;
  }

  const handleNavigate = (route: string) => {
    onClose();
    navigate(route);
  };

  const handleFutureItem = (name: string) => {
    toast.info(`${name} — 백엔드 미지원 (예정)`);
  };

  const implementedApps = SCREENS.filter((screen) => screen.isImplemented);

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex h-11 items-center justify-between px-3">
        <span className="text-[13px] font-semibold">Foundry</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="네비게이션 닫기"
          className="rounded p-1 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground"
        >
          <X className="size-3.5" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-2 pb-3">
        <div className="space-y-0.5 border-b border-sidebar-border pb-2.5">
          <SidebarRow
            icon={<Home className="size-4" />}
            label="Home"
            isActive={pathname === "/"}
            onClick={() => handleNavigate("/")}
          />
          <SidebarRow
            icon={<Search className="size-4" />}
            label="Search..."
            hint="⌘J"
            onClick={() => {
              onClose();
              onOpenSearch();
            }}
          />
          <NotificationsButton variant="sidebar" />
          <SidebarRow
            icon={<Gift className="size-4" />}
            label="What's New"
            onClick={() => handleFutureItem("What's New")}
          />
        </div>

        <div className="space-y-0.5 border-b border-sidebar-border pb-2.5">
          <SidebarRow
            icon={<Clock className="size-4" />}
            label="Recent"
            isActive={isRecentOpen}
            onClick={() => setIsRecentOpen((previous) => !previous)}
          />
          {isRecentOpen ? (
            recentVisits.length === 0 ? (
              <p className="px-8 py-1 text-[11px] text-sidebar-foreground/40">
                최근 방문 기록이 없습니다 (로컬 저장).
              </p>
            ) : (
              <div className="space-y-0.5 pl-5">
                {recentVisits.slice(0, MAX_SIDEBAR_RECENTS).map((visit) => (
                  <NavLink
                    key={visit.route}
                    to={visit.route}
                    onClick={onClose}
                    className="flex items-center gap-2 rounded px-2 py-1 text-xs text-sidebar-foreground/75 hover:bg-sidebar-accent hover:text-sidebar-foreground"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {visit.title}
                    </span>
                  </NavLink>
                ))}
              </div>
            )
          ) : null}
          <SidebarRow
            icon={<FolderClosed className="size-4" />}
            label="Files"
            isActive={
              pathname === "/projects" || pathname.startsWith("/projects/")
            }
            onClick={() => handleNavigate("/projects")}
          />
          <SidebarRow
            icon={<Grid3x3 className="size-4" />}
            label="Applications"
            onClick={() => handleNavigate("/")}
          />
        </div>

        <div className="border-b border-sidebar-border pb-2.5">
          <SectionLabel
            label="Applications"
            action={
              <button
                type="button"
                onClick={() => handleNavigate("/")}
                className="text-[11px] text-sidebar-foreground/55 hover:text-sidebar-foreground"
              >
                View all
              </button>
            }
          />
          <div className="space-y-0.5">
            {implementedApps.map((screen) => (
              <AppRow
                key={screen.id}
                screen={screen}
                onNavigate={handleNavigate}
              />
            ))}
          </div>
        </div>

        <div className="border-b border-sidebar-border pb-2.5">
          <SectionLabel label="Files" />
          {error ? (
            <p className="px-2 py-1 text-[11px] text-destructive">
              즐겨찾기를 불러오지 못했습니다.
            </p>
          ) : isLoading && favorites.length === 0 ? (
            <p className="px-2 py-1 text-[11px] text-sidebar-foreground/40">
              불러오는 중...
            </p>
          ) : favorites.length === 0 ? (
            <p className="px-2 py-1 text-[11px] text-sidebar-foreground/40">
              리소스에서 별을 눌러 추가
            </p>
          ) : (
            <div className="space-y-0.5">
              {favorites.slice(0, MAX_SIDEBAR_FAVORITES).map((resource) => (
                <FavoriteRow
                  key={resource.rid}
                  resource={resource}
                  onNavigate={handleNavigate}
                  onToggleFavorite={(rid) => void toggleFavorite(rid)}
                />
              ))}
            </div>
          )}
        </div>

        <div className="space-y-0.5">
          <SidebarRow
            icon={<Sparkles className="size-4" />}
            label="AIP Assist"
            isActive={pathname === "/aip" || pathname.startsWith("/aip/")}
            onClick={() => handleNavigate("/aip")}
          />
          <SidebarRow
            icon={<HelpCircle className="size-4" />}
            label="Support"
            onClick={() => handleFutureItem("Support")}
          />
        </div>
      </div>
      <div className="flex items-center gap-2 border-t border-sidebar-border px-3 py-2.5">
        <Avatar className="size-6">
          <AvatarFallback className="bg-sidebar-primary text-[10px] text-sidebar-primary-foreground">
            {DEMO_CONTEXT.userId?.slice(0, 2).toUpperCase()}
          </AvatarFallback>
        </Avatar>
        <div className="min-w-0">
          <div className="truncate text-[11px] font-medium">
            {DEMO_CONTEXT.userId}
          </div>
          <div className="truncate text-[10px] text-sidebar-foreground/50">
            {DEMO_CONTEXT.tenantId} · {DEMO_CONTEXT.roles?.join(", ")}
          </div>
        </div>
      </div>
    </aside>
  );
}

function FavoriteRow({
  resource,
  onNavigate,
  onToggleFavorite,
}: {
  resource: ResourceItem;
  onNavigate: (route: string) => void;
  onToggleFavorite: (rid: string) => void;
}) {
  return (
    <div className="group flex items-center gap-2 rounded px-2 py-1.5 text-xs text-sidebar-foreground/85 hover:bg-sidebar-accent hover:text-sidebar-foreground">
      <button
        type="button"
        onClick={() => onNavigate(resource.operationsPath ?? "/projects")}
        className="flex min-w-0 flex-1 items-center gap-2 text-left"
      >
        <span className="min-w-0 flex-1 truncate">{resource.displayName}</span>
        <span className="shrink-0 font-mono text-[10px] text-sidebar-foreground/40">
          {resource.resourceType}
        </span>
      </button>
      <button
        type="button"
        aria-label={`${resource.displayName} 즐겨찾기 해제`}
        onClick={() => onToggleFavorite(resource.rid)}
        className="shrink-0 rounded p-0.5 hover:bg-sidebar-border"
      >
        <Star className="size-3 fill-warning text-warning" />
      </button>
    </div>
  );
}
