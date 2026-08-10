import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersOptions {
  /** Initial URL (with any query string), e.g. "/servers/7". */
  route?: string;
  /** Router path pattern, e.g. "/servers/:id". Required if the component under test uses useParams. */
  path?: string;
  queryClient?: QueryClient;
}

/**
 * Wraps a component under test with a fresh QueryClientProvider (retries
 * disabled) and a MemoryRouter. Does NOT include AuthProvider -- most page
 * tests mock `@/auth/AuthContext`'s `useAuth` directly instead, since these
 * pages only read a handful of fields (`token`, `isAdmin`, `user`) and full
 * session-storage-driven login flow isn't relevant to them. Tests that
 * exercise the real auth flow (LoginPage, AuthContext) use the real
 * AuthProvider directly instead of this helper.
 */
export function renderWithProviders(ui: ReactElement, options: RenderWithProvidersOptions = {}) {
  const { route = "/", path, queryClient = createTestQueryClient() } = options;

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          {path ? <Routes>{<Route path={path} element={children as ReactElement} />}</Routes> : children}
        </MemoryRouter>
      </QueryClientProvider>
    );
  }

  return { queryClient, ...render(ui, { wrapper: Wrapper }) };
}
