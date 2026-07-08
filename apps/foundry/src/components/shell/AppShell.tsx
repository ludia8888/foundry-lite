import { Suspense, useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router";

import { LoadingState } from "@/components/shared/LoadingState";
import { getScreenByRoute } from "@/lib/screens";

import { CommandPalette } from "@/components/shell/CommandPalette";
import { IconRail } from "@/components/shell/IconRail";
import { NavSidebar } from "@/components/shell/NavSidebar";
import { createRecentVisit } from "@/components/shell/recent-visits";
import { TopBar } from "@/components/shell/TopBar";

/** Global Navigation 앱 셸: 다크 아이콘 레일 + 확장 사이드바 + 상단 툴바 + 콘텐츠. */
export function AppShell() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const { pathname } = useLocation();

  useEffect(() => {
    const screen = getScreenByRoute(pathname);
    if (!screen) return;
    createRecentVisit({
      screenId: screen.id,
      title: screen.title,
      route: pathname,
    });
  }, [pathname]);

  return (
    <div className="flex h-full">
      <IconRail
        onToggleSidebar={() => setIsSidebarOpen((previous) => !previous)}
        onOpenSearch={() => setIsSearchOpen(true)}
      />
      <NavSidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        onOpenSearch={() => setIsSearchOpen(true)}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenSearch={() => setIsSearchOpen(true)} />
        <main className="min-h-0 flex-1 overflow-auto">
          <Suspense fallback={<LoadingState className="p-6" />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <CommandPalette isOpen={isSearchOpen} onOpenChange={setIsSearchOpen} />
    </div>
  );
}
