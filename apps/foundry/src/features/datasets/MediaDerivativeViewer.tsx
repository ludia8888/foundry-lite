import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";

import { useScreenQuery } from "./use-screen-query";

interface MediaDerivativeViewerProps {
  mediaDerivativeId: string;
}

/** media derivative(처리 결과) 원본 payload를 접이식 JSON evidence로 보여준다. */
export function MediaDerivativeViewer({
  mediaDerivativeId,
}: MediaDerivativeViewerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const client = useFoundryLiteClient();
  const loadDerivative = useCallback(
    () => client.media.derivatives.get(mediaDerivativeId),
    [client, mediaDerivativeId],
  );
  const query = useScreenQuery(
    ["media", "derivatives", mediaDerivativeId],
    loadDerivative,
    { enabled: isOpen },
  );

  return (
    <div className="rounded border">
      <button
        type="button"
        onClick={() => setIsOpen((previous) => !previous)}
        className="flex h-8 w-full items-center gap-1.5 px-3 text-left hover:bg-muted/60"
      >
        {isOpen ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <span className="section-label">파생 결과 상세</span>
        <span
          className="ml-auto truncate font-mono text-[11px] text-muted-foreground"
          title={mediaDerivativeId}
        >
          {mediaDerivativeId}
        </span>
      </button>
      {isOpen ? (
        <div className="border-t p-2">
          {query.isLoading ? (
            <LoadingState rowCount={3} />
          ) : query.error ? (
            <ErrorState error={query.error} onRetry={query.reload} />
          ) : (
            <pre className="max-h-64 overflow-auto rounded bg-muted/40 p-2 font-mono text-[11px] whitespace-pre-wrap">
              {JSON.stringify(query.data, null, 2)}
            </pre>
          )}
        </div>
      ) : null}
    </div>
  );
}
