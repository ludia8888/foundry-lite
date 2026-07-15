import { Copy } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function SettingsCard({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded border">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b bg-muted/15 px-4 py-3">
        <div>
          <h3 className="text-[13px] font-semibold">{title}</h3>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {description}
          </p>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function SettingsRow({
  label,
  value,
  isCode = false,
}: {
  label: string;
  value: string;
  isCode?: boolean;
}) {
  return (
    <div className="grid gap-1 px-4 py-2.5 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-4">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "min-w-0 break-words text-xs",
          isCode && "font-mono text-[11px]",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

export function CopyableValue({ value }: { value: string }) {
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success("클립보드에 복사했습니다");
    } catch {
      toast.error("복사에 실패했습니다");
    }
  };

  return (
    <span className="flex items-center gap-1">
      <span className="truncate font-mono text-[11px]">{value}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-5 shrink-0"
        onClick={() => void handleCopy()}
        aria-label="구성 지문 복사"
      >
        <Copy className="size-3" />
      </Button>
    </span>
  );
}
