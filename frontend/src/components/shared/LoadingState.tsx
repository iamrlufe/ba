import { Skeleton } from "@/components/ui/skeleton";

/** Table-shaped skeleton for list pages. */
export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div key={rowIdx} className="flex gap-4">
          {Array.from({ length: columns }).map((_, colIdx) => (
            <Skeleton key={colIdx} className="h-8 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Card-shaped skeleton for detail pages. */
export function CardSkeleton() {
  return (
    <div className="space-y-4 rounded-lg border p-6">
      <Skeleton className="h-6 w-1/3" />
      <Skeleton className="h-4 w-2/3" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  );
}

/** Bare centered spinner, for full-page contexts like RunDetailPage's initial load. */
export function FullPageSpinner() {
  return (
    <div className="flex h-64 w-full items-center justify-center">
      <div
        className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent"
        role="status"
        aria-label="Loading"
      />
    </div>
  );
}
