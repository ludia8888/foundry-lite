import { Calendar, Check, Hash, Quote, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/** 속성 데이터 타입 → 리스트 아이콘 매핑 (415dd 드롭다운 대응). */
const TYPE_ICONS: Record<string, LucideIcon> = {
  string: Quote,
  integer: Hash,
  float: Hash,
  boolean: Check,
  date: Calendar,
  timestamp: Calendar,
};

/** 속성 타입 아이콘: 문자열=인용부호, 숫자=#, 불리언=✓, 날짜=달력. */
export function PropertyTypeIcon({
  dataType,
  className,
}: {
  dataType: string;
  className?: string;
}) {
  const Icon = TYPE_ICONS[dataType] ?? Quote;
  return (
    <Icon className={cn("size-3 shrink-0 text-muted-foreground", className)} />
  );
}
