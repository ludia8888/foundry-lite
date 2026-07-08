import { Shapes } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { isActiveOntologyMissingError } from "@/lib/errors";

import { EmptyState } from "./EmptyState";

export { isActiveOntologyMissingError };

export function OntologyRequiredState({
  className,
}: {
  className?: string;
}) {
  return (
    <EmptyState
      icon={Shapes}
      title="활성 온톨로지가 없습니다"
      description="객체 탐색, 액션 실행, Workshop 런타임은 활성 온톨로지 위에서 작동합니다. Ontology Manager에서 첫 버전을 적용하면 이 화면이 바로 데이터와 연결됩니다."
      className={className}
      action={
        <Button asChild size="sm">
          <Link to="/ontology">Ontology Manager 열기</Link>
        </Button>
      }
    />
  );
}
