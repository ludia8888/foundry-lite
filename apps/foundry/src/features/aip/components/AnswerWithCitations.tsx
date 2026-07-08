import { type AgentCitationView, toAnswerSegments } from "../aip-model";

/** 답변 텍스트 + inline citation anchor([n]) 렌더. anchor 클릭 시 카드 선택. */
export function AnswerWithCitations({
  answer,
  citations,
  onSelectCitation,
}: {
  answer: string;
  citations: readonly AgentCitationView[];
  onSelectCitation: (order: number) => void;
}) {
  const segments = toAnswerSegments(answer, citations);
  return (
    <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-foreground">
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          return <span key={index}>{segment.text}</span>;
        }
        return (
          <span key={index}>
            {segment.text}
            <button
              type="button"
              aria-label={`근거 ${segment.order}`}
              onClick={() => onSelectCitation(segment.order)}
              className="mx-0.5 inline-flex h-4 items-center rounded-sm bg-primary/10 px-1 align-baseline font-mono text-[10px] font-semibold text-primary hover:bg-primary/20"
            >
              [{segment.order}]
            </button>
          </span>
        );
      })}
    </p>
  );
}
