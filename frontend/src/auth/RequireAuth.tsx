import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

/**
 * Wraps every route except `/login`. Redirects to `/login` (preserving the
 * attempted location as `from`) if there's no valid session; shows nothing
 * (AppShell handles the loading skeleton) while the initial token
 * validation against GET /api/auth/me is still in flight.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { token, user, isLoading } = useAuth();
  const location = useLocation();

  if (token && isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="text-sm text-muted-foreground">Loading…</div>
      </div>
    );
  }

  if (!token || !user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
