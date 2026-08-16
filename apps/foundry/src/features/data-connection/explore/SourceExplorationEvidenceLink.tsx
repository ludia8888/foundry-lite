import type { FoundryLiteApiError } from "@foundry-lite/sdk";
import { Link } from "react-router";

import { cn } from "@/lib/utils";

import { sourceExplorationEvidence } from "../source-model";

export function SourceExplorationEvidenceLink({
  result,
  error,
  className,
  label = "Operations에서 실행 증거 보기",
}: {
  result?: { explorationRunId: string; operationsPath: string | null } | null;
  error?: FoundryLiteApiError | null;
  className?: string;
  label?: string;
}) {
  const evidence = sourceExplorationEvidence(result, error?.details);
  if (!evidence) return null;
  return (
    <Link
      to={evidence.href}
      className={cn("font-medium text-primary hover:underline", className)}
    >
      {label} · <span className="font-mono">{evidence.runId}</span>
    </Link>
  );
}
