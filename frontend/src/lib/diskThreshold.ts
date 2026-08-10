import type { DiskRead } from "@/api/types";

export type DiskUsageLevel = "unknown" | "ok" | "warning" | "critical";

export function diskUsageLevel(
  disk: Pick<DiskRead, "used_pct" | "warning_threshold_pct" | "critical_threshold_pct">,
): DiskUsageLevel {
  if (disk.used_pct == null) return "unknown";
  if (disk.used_pct >= disk.critical_threshold_pct) return "critical";
  if (disk.used_pct >= disk.warning_threshold_pct) return "warning";
  return "ok";
}
