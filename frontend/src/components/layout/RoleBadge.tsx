import { Badge } from "@/components/ui/badge";
import type { UserRole } from "@/api/types";

export function RoleBadge({ role }: { role: UserRole }) {
  return <Badge variant={role === "ADMIN" ? "default" : "secondary"}>{role}</Badge>;
}
