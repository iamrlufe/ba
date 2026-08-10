import { apiFetch, buildQuery } from "../client";
import type {
  PaginatedResponse,
  RestoreOperationCreate,
  RestoreOperationLogRead,
  RestoreOperationRead,
  RestoreOperationUpdate,
} from "../types";

export interface ListRestoreOperationsParams {
  [key: string]: string | number | boolean | undefined;
  status?: string;
  sql_instance_id?: number;
  backup_record_id?: number;
  limit?: number;
  offset?: number;
}

export async function listRestoreOperations(
  token: string | null,
  params: ListRestoreOperationsParams = {},
): Promise<PaginatedResponse<RestoreOperationRead>> {
  return apiFetch<PaginatedResponse<RestoreOperationRead>>(`/restore-operations${buildQuery(params)}`, { token });
}

export async function getRestoreOperation(token: string | null, id: number): Promise<RestoreOperationRead> {
  return apiFetch<RestoreOperationRead>(`/restore-operations/${id}`, { token });
}

export async function getRestoreOperationLog(token: string | null, id: number): Promise<RestoreOperationLogRead> {
  return apiFetch<RestoreOperationLogRead>(`/restore-operations/${id}/log`, { token });
}

export async function createRestoreOperation(
  token: string | null,
  payload: RestoreOperationCreate,
): Promise<RestoreOperationRead> {
  return apiFetch<RestoreOperationRead>("/restore-operations", { method: "POST", body: payload, token });
}

/** ADMIN-only -- used to cancel a PENDING restore operation (status -> CANCELLED). */
export async function updateRestoreOperation(
  token: string | null,
  id: number,
  payload: RestoreOperationUpdate,
): Promise<RestoreOperationRead> {
  return apiFetch<RestoreOperationRead>(`/restore-operations/${id}`, { method: "PATCH", body: payload, token });
}
