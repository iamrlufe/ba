import { apiFetch, buildQuery } from "../client";
import type { PaginatedResponse, SqlInstanceCreate, SqlInstanceRead, SqlInstanceUpdate } from "../types";

export interface ListSqlInstancesParams {
  [key: string]: string | number | boolean | undefined;
  status?: string;
  server_id?: number;
  include_deleted?: boolean;
  limit?: number;
  offset?: number;
}

export async function listSqlInstances(
  token: string | null,
  params: ListSqlInstancesParams = {},
): Promise<PaginatedResponse<SqlInstanceRead>> {
  return apiFetch<PaginatedResponse<SqlInstanceRead>>(`/sql-instances${buildQuery(params)}`, { token });
}

export async function getSqlInstance(token: string | null, id: number): Promise<SqlInstanceRead> {
  return apiFetch<SqlInstanceRead>(`/sql-instances/${id}`, { token });
}

export async function createSqlInstance(token: string | null, payload: SqlInstanceCreate): Promise<SqlInstanceRead> {
  return apiFetch<SqlInstanceRead>("/sql-instances", { method: "POST", body: payload, token });
}

export async function updateSqlInstance(
  token: string | null,
  id: number,
  payload: SqlInstanceUpdate,
): Promise<SqlInstanceRead> {
  return apiFetch<SqlInstanceRead>(`/sql-instances/${id}`, { method: "PATCH", body: payload, token });
}

export async function deleteSqlInstance(token: string | null, id: number): Promise<void> {
  await apiFetch<void>(`/sql-instances/${id}`, { method: "DELETE", token });
}
