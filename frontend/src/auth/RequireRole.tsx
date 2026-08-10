import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import type { UserRole } from "@/api/types";

/**
 * Route-level role gate (UX only -- the backend re-checks on every
 * mutation regardless). Redirects to /not-authorized if the current user's
 * role isn't in `roles`.
 */
export function RequireRole({ roles, children }: { roles: UserRole[]; children: ReactNode }) {
  const { user } = useAuth();

  if (!user || !roles.includes(user.role)) {
    return <Navigate to="/not-authorized" replace />;
  }

  return <>{children}</>;
}
