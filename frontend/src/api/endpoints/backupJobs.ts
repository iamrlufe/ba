import { apiFetch, buildQuery } from "../client";
import type { BackupJobCreate, BackupJobRead, BackupJobUpdate, PaginatedResponse, VerificationRunRead } from "../types";

export interface ListBackupJobsParams {
  [key: string]: string | number | boolean | undefined;
  server_id?: number;
  disk_id?: number;
  sql_instance_id?: number;
  is_enabled?: boolean;
  limit?: number;
  offset?: number;
}

export async function listBackupJobs(
  token: string | null,
  params: ListBackupJobsParams = {},
): Promise<PaginatedResponse<BackupJobRead>> {
  return apiFetch<PaginatedResponse<BackupJobRead>>(`/backup-jobs${buildQuery(params)}`, { token });
}

export async function getBackupJob(token: string | null, id: number): Promise<BackupJobRead> {
  return apiFetch<BackupJobRead>(`/backup-jobs/${id}`, { token });
}

export async function createBackupJob(token: string | null, payload: BackupJobCreate): Promise<BackupJobRead> {
  return apiFetch<BackupJobRead>("/backup-jobs", { method: "POST", body: payload, token });
}

export async function updateBackupJob(
  token: string | null,
  id: number,
  payload: BackupJobUpdate,
): Promise<BackupJobRead> {
  return apiFetch<BackupJobRead>(`/backup-jobs/${id}`, { method: "PATCH", body: payload, token });
}

export async function deleteBackupJob(token: string | null, id: number): Promise<void> {
  await apiFetch<void>(`/backup-jobs/${id}`, { method: "DELETE", token });
}

export async function verifyBackupJob(token: string | null, id: number): Promise<VerificationRunRead> {
  return apiFetch<VerificationRunRead>(`/backup-jobs/${id}/verify`, { method: "POST", token });
}
