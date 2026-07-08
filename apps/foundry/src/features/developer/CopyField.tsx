import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CopyFieldProps {
  label: string;
  value: string;
  /** 민감 값(secret)일 때 경고 카피를 함께 노출한다. */
  sensitiveNote?: string;
  className?: string;
}

/**
 * Palantir "registered application" 화면의 Client ID/secret 필드 패턴:
 * 라벨 + mono 값 + 복사 아이콘. secret은 재조회 불가 경고를 함께 보여준다.
 */
export function CopyField({
  label,
  value,
  sensitiveNote,
  className,
}: CopyFieldProps) {
  const [isCopied, setIsCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setIsCopied(true);
      window.setTimeout(() => setIsCopied(false), 1500);
    } catch {
      setIsCopied(false);
    }
  };

  return (
    <div className={cn("space-y-1", className)}>
      <div className="section-label">{label}</div>
      <div className="flex items-center gap-1.5 rounded border bg-muted/40 px-2 py-1">
        <code className="min-w-0 flex-1 truncate font-mono text-[11px]">
          {value}
        </code>
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          onClick={() => void handleCopy()}
          aria-label={`${label} 복사`}
        >
          {isCopied ? (
            <Check className="text-success" />
          ) : (
            <Copy className="text-muted-foreground" />
          )}
        </Button>
      </div>
      {sensitiveNote ? (
        <p className="text-[11px] text-destructive">{sensitiveNote}</p>
      ) : null}
    </div>
  );
}
