import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/auth/RequireAuth";
import { RequireRole } from "@/auth/RequireRole";
import { LoginPage } from "@/pages/LoginPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { ServersListPage } from "@/pages/ServersListPage";
import { ServerDetailPage } from "@/pages/ServerDetailPage";
import { JobsListPage } from "@/pages/JobsListPage";
import { JobDetailPage } from "@/pages/JobDetailPage";
import { JobFormPage } from "@/pages/JobFormPage";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { AlertsPage } from "@/pages/AlertsPage";
import { RestorePage } from "@/pages/RestorePage";
import { SqlInstancesPage } from "@/pages/SqlInstancesPage";
import { NotAuthorizedPage } from "@/pages/NotAuthorizedPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

function withShell(children: React.ReactNode) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
    </RequireAuth>
  );
}

export const router = createBrowserRouter([
  { path: "/", element: <Navigate to="/dashboard" replace /> },
  { path: "/login", element: <LoginPage /> },
  { path: "/dashboard", element: withShell(<DashboardPage />) },
  { path: "/servers", element: withShell(<ServersListPage />) },
  { path: "/servers/:id", element: withShell(<ServerDetailPage />) },
  { path: "/jobs", element: withShell(<JobsListPage />) },
  { path: "/jobs/new", element: withShell(<RequireRole roles={["ADMIN"]}><JobFormPage mode="create" /></RequireRole>) },
  { path: "/jobs/:id", element: withShell(<JobDetailPage />) },
  { path: "/jobs/:id/edit", element: withShell(<RequireRole roles={["ADMIN"]}><JobFormPage mode="edit" /></RequireRole>) },
  { path: "/runs/:id", element: withShell(<RunDetailPage />) },
  { path: "/alerts", element: withShell(<AlertsPage />) },
  { path: "/restore", element: withShell(<RestorePage />) },
  { path: "/sql-instances", element: withShell(<SqlInstancesPage />) },
  { path: "/not-authorized", element: withShell(<NotAuthorizedPage />) },
  { path: "*", element: withShell(<NotFoundPage />) },
]);
