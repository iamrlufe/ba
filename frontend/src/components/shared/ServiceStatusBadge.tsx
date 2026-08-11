import { Badge } from "@/components/ui/badge";
import type { BadgeVariant } from "@/lib/statusStyles";

// `status` is free text owned by .NET's ServiceControllerStatus (Running,
// Stopped, StartPending, PausePending, ...), not a closed backend enum --
// unlike the Record<Enum, Variant> lookups in statusStyles.ts, we can't
// exhaustively map every value, so this falls through to a neutral
// "secondary" variant for anything unrecognized.
const OK_STATUSES = new Set(["Running"]);
const BAD_STATUSES = new Set(["Stopped"]);

export function ServiceStatusBadge({ status }: { status: string }) {
  const variant: BadgeVariant = OK_STATUSES.has(status) ? "success" : BAD_STATUSES.has(status) ? "destructive" : "secondary";
  return <Badge variant={variant}>{status}</Badge>;
}
