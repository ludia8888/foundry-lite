import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface LoadingStateProps {
  rowCount?: number;
  className?: string;
}

export function LoadingState({ rowCount = 5, className }: LoadingStateProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rowCount }, (_, index) => (
        <Skeleton key={index} className="h-7 w-full" />
      ))}
    </div>
  );
}
