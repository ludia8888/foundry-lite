import { Search } from "lucide-react";
import { Link, useLocation } from "react-router";

import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { NotificationsButton } from "@/components/shell/NotificationsButton";
import { getScreenByRoute } from "@/lib/screens";

interface TopBarProps {
  onOpenSearch: () => void;
}

/** 흰색 상단 툴바: 브레드크럼 + 중앙 검색 + 우측 요청 telemetry. */
export function TopBar({ onOpenSearch }: TopBarProps) {
  const { pathname } = useLocation();
  const screen = getScreenByRoute(pathname);

  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b bg-card px-3">
      <div className="flex min-w-0 flex-1 items-center gap-1.5 text-[13px]">
        <Link to="/" className="text-muted-foreground hover:text-foreground">
          Foundry
        </Link>
        {screen ? (
          <>
            <span className="text-muted-foreground/50">/</span>
            <span className="flex items-center gap-1.5 truncate font-medium">
              <screen.icon className="size-3.5 text-muted-foreground" />
              {screen.title}
            </span>
          </>
        ) : null}
      </div>
      <button
        type="button"
        aria-label="Quicksearch 열기"
        onClick={onOpenSearch}
        className="flex h-7 w-64 items-center gap-2 rounded border bg-muted px-2.5 text-xs text-muted-foreground hover:bg-accent"
      >
        <Search className="size-3.5" />
        <span className="flex-1 text-left">리소스, 화면 검색...</span>
        <kbd className="rounded border bg-card px-1 font-mono text-[10px]">
          ⌘K
        </kbd>
      </button>
      <div className="flex flex-1 items-center justify-end gap-2">
        <NotificationsButton />
        <RequestTelemetry />
      </div>
    </header>
  );
}
