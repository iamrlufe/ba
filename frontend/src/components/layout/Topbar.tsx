import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/auth/AuthContext";
import { RoleBadge } from "./RoleBadge";

export function Topbar() {
  const { user, logout } = useAuth();

  return (
    <header className="flex h-14 items-center justify-between border-b px-4">
      <span className="font-semibold">Backup Orchestrator</span>
      {user ? (
        <div className="flex items-center gap-3">
          <span className="text-sm text-muted-foreground">{user.username}</span>
          <RoleBadge role={user.role} />
          <Button variant="ghost" size="sm" onClick={logout}>
            <LogOut className="mr-1 h-4 w-4" />
            Log out
          </Button>
        </div>
      ) : null}
    </header>
  );
}
