import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { getMe, login as loginRequest } from "@/api/endpoints/auth";
import { ApiError } from "@/api/client";
import type { LoginRequest, UserRead } from "@/api/types";

const TOKEN_STORAGE_KEY = "bo_token";

interface AuthContextValue {
  token: string | null;
  user: UserRead | null;
  /** True while the initial sessionStorage-token validation (GET /auth/me) is in flight. */
  isLoading: boolean;
  /**
   * Single source of truth for "is this user an admin", so role-gated UI
   * doesn't reinvent `user?.role === "ADMIN"` at every call site (a
   * pattern that silently drifts if a third role is ever added). This is
   * a UX convenience only -- the backend independently enforces every
   * admin-only action regardless of what this hook returns.
   */
  isAdmin: boolean;
  login: (credentials: LoginRequest) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  // Deliberate, user-confirmed deviation from a pure in-memory JWT default:
  // the token is persisted in sessionStorage (not localStorage) so a page
  // reload doesn't force a re-login. This makes XSS-avoidance in rendering
  // (never dangerouslySetInnerHTML, always JSX text nodes) the load-bearing
  // mitigation -- see the security requirement in the project brief.
  const [token, setToken] = useState<string | null>(() => sessionStorage.getItem(TOKEN_STORAGE_KEY));
  const [user, setUser] = useState<UserRead | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(() => sessionStorage.getItem(TOKEN_STORAGE_KEY) !== null);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    getMe(token)
      .then((me) => {
        if (!cancelled) {
          setUser(me);
          setIsLoading(false);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          sessionStorage.removeItem(TOKEN_STORAGE_KEY);
          setToken(null);
          setUser(null);
        }
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Only re-run when the token identity changes (login/logout).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const login = useCallback(async (credentials: LoginRequest) => {
    const response = await loginRequest(credentials);
    const me = await getMe(response.access_token);
    sessionStorage.setItem(TOKEN_STORAGE_KEY, response.access_token);
    setToken(response.access_token);
    setUser(me);
  }, []);

  const logout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const isAdmin = user?.role === "ADMIN";

  const value = useMemo(
    () => ({ token, user, isLoading, isAdmin, login, logout }),
    [token, user, isLoading, isAdmin, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
