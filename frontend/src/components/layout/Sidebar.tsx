import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Server,
  ListChecks,
  Bell,
  DatabaseBackup,
  Database,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/servers", label: "Servers", icon: Server },
  { to: "/jobs", label: "Backup jobs", icon: ListChecks },
  { to: "/alerts", label: "Alerts", icon: Bell },
  { to: "/restore", label: "Restore", icon: DatabaseBackup },
  { to: "/sql-instances", label: "SQL instances", icon: Database },
];

export function Sidebar() {
  return (
    <aside className="hidden w-56 shrink-0 border-r bg-muted/30 md:block">
      <nav className="flex flex-col gap-1 p-4">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground",
                isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
