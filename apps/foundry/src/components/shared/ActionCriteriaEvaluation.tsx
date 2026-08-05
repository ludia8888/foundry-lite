import type {
  ActionCriteriaEvaluation,
  ActionCriteriaEvaluationNode,
  ActionCriteriaValueSource,
} from "@foundry-lite/sdk";
import { CheckCircle2, CircleSlash2, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";

export function ActionCriteriaEvaluationPanel({
  evaluation,
}: {
  evaluation: ActionCriteriaEvaluation | null;
}) {
  if (!evaluation) return null;
  if (!evaluation.tree) {
    return (
      <div className="flex items-center gap-1.5 rounded border bg-muted/30 p-2 text-[11px] text-muted-foreground">
        <CircleSlash2 className="size-3" />
        제출 기준을 아직 판정하지 않았습니다: {evaluation.reason ?? "선행 검증 필요"}
      </div>
    );
  }
  return (
    <div
      role="region"
      className="space-y-1 rounded border bg-background/70 p-2"
      aria-label="제출 기준 판정"
    >
      <div className="text-[11px] font-semibold">제출 기준 상세 판정</div>
      <CriteriaNode node={evaluation.tree} depth={0} />
    </div>
  );
}

function CriteriaNode({ node, depth }: { node: ActionCriteriaEvaluationNode; depth: number }) {
  const Icon = node.isSatisfied ? CheckCircle2 : XCircle;
  return (
    <div className={cn("space-y-1", depth > 0 && "ml-4 border-l pl-2")}>
      <div className="flex items-start gap-1.5 text-[11px]">
        <Icon className={cn("mt-0.5 size-3 shrink-0", node.isSatisfied ? "text-success" : "text-destructive")} />
        <div>
          <span className="font-mono">{criteriaNodeLabel(node)}</span>
          {node.message ? <span className="ml-1 text-muted-foreground">· {node.message}</span> : null}
        </div>
      </div>
      {node.children?.map((child) => <CriteriaNode key={child.path} node={child} depth={depth + 1} />)}
    </div>
  );
}

function criteriaNodeLabel(node: ActionCriteriaEvaluationNode): string {
  if (node.kind === "all") return `ALL · ${node.isSatisfied ? "모두 충족" : "일부 불충족"}`;
  if (node.kind === "any") return `ANY · ${node.isSatisfied ? "하나 이상 충족" : "모두 불충족"}`;
  if (node.kind === "not") return `NOT · ${node.isSatisfied ? "충족" : "불충족"}`;
  const right = node.right ? ` ${sourceLabel(node.right)}` : "";
  return `${sourceLabel(node.left)} ${node.operator ?? "?"}${right} · ${
    node.isSatisfied ? "충족" : "불충족"
  }`;
}

function sourceLabel(source: ActionCriteriaValueSource | undefined): string {
  if (!source) return "?";
  if (source.kind === "literal") return JSON.stringify(source.literal);
  if (source.kind === "parameter") return `parameter.${String(source.reference ?? "?")}`;
  if (source.kind === "objectProperty") return `object.${String(source.reference ?? "?")}`;
  if (source.kind === "currentUser") return `user.${String(source.reference ?? "id")}`;
  if (source.kind === "linkedObjectProperty") return linkedSourceLabel(source.reference);
  return source.kind;
}

function linkedSourceLabel(reference: unknown): string {
  if (!reference || typeof reference !== "object" || Array.isArray(reference)) return "linked.?";
  const value = reference as Record<string, unknown>;
  const linkType = String(value.linkType ?? "?");
  const direction = String(value.direction ?? "outgoing");
  const property = String(value.property ?? "?");
  const aggregation = String(value.aggregation ?? "values");
  return `linked.${linkType}.${direction}.${property}.${aggregation}`;
}
