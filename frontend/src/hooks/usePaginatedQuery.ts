import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { PaginatedResponse } from "@/api/types";

export interface UsePaginatedQueryOptions<T> {
  /** Base query key; `{ limit, offset }` is appended automatically. */
  queryKey: readonly unknown[];
  queryFn: (params: { limit: number; offset: number }) => Promise<PaginatedResponse<T>>;
  defaultLimit?: number;
  enabled?: boolean;
}

export type UsePaginatedQueryResult<T> = UseQueryResult<PaginatedResponse<T>> & {
  limit: number;
  offset: number;
  setOffset: (offset: number) => void;
};

/**
 * Wraps useQuery for a PaginatedResponse<T> endpoint, keeping `limit`/`offset`
 * in the URL's search params (?limit=&offset=) so list views are
 * shareable/bookmarkable. Callers own their own filter search params
 * separately and fold them into `queryKey`/`queryFn`.
 */
export function usePaginatedQuery<T>({
  queryKey,
  queryFn,
  defaultLimit = 20,
  enabled = true,
}: UsePaginatedQueryOptions<T>): UsePaginatedQueryResult<T> {
  const [searchParams, setSearchParams] = useSearchParams();

  const offsetParam = Number(searchParams.get("offset") ?? "0");
  const limitParam = Number(searchParams.get("limit") ?? String(defaultLimit));
  const offset = Number.isFinite(offsetParam) && offsetParam >= 0 ? offsetParam : 0;
  const limit = Number.isFinite(limitParam) && limitParam > 0 ? limitParam : defaultLimit;

  const query = useQuery({
    queryKey: [...queryKey, { limit, offset }],
    queryFn: () => queryFn({ limit, offset }),
    enabled,
    placeholderData: keepPreviousData,
  });

  function setOffset(newOffset: number) {
    const next = new URLSearchParams(searchParams);
    next.set("offset", String(Math.max(0, newOffset)));
    setSearchParams(next, { replace: true });
  }

  return { ...query, limit, offset, setOffset };
}
