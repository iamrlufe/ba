import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { diskUsageLevel } from "@/lib/diskThreshold";
import type { DiskRead } from "@/api/types";

const levelIndicatorClass: Record<ReturnType<typeof diskUsageLevel>, string> = {
  unknown: "bg-muted-foreground/40 bg-[repeating-linear-gradient(45deg,transparent,transparent_4px,hsl(var(--muted-foreground)/0.3)_4px,hsl(var(--muted-foreground)/0.3)_8px)]",
  ok: "bg-success",
  warning: "bg-warning",
  critical: "bg-destructive",
};

export function DiskUsageBar({
  disk,
}: {
  disk: Pick<DiskRead, "used_pct" | "warning_threshold_pct" | "critical_threshold_pct">;
}) {
  const level = diskUsageLevel(disk);
  const label = disk.used_pct == null ? "no data yet" : `${disk.used_pct}%`;

  return (
    <div className="flex items-center gap-2" title={disk.used_pct == null ? "no data yet" : undefined}>
      <Progress
        value={disk.used_pct ?? 100}
        indicatorClassName={cn(levelIndicatorClass[level])}
        className={cn(level === "unknown" && "opacity-70")}
      />
      <span className="w-14 shrink-0 text-right text-xs text-muted-foreground">{label}</span>
    </div>
  );
}
