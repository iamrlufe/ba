import { apiFetch, buildQuery } from "../client";
import type { BackupRecordRead, PaginatedResponse } from "../types";

export interface ListBackupRecordsParams {
  [key: string]: string | number | boolean | undefined;
  backup_job_id?: number;
  remote_path?: string;
  file_name?: string;
  limit?: number;
  offset?: number;
}

/** ADMIN-only (or agent key) -- see app/routers/backup_records.py::list_backup_records. */
export async function listBackupRecords(
  token: string | null,
  params: ListBackupRecordsParams = {},
): Promise<PaginatedResponse<BackupRecordRead>> {
  return apiFetch<PaginatedResponse<BackupRecordRead>>(`/backup-records${buildQuery(params)}`, { token });
}

/** Open to any authenticated role -- used for the OPERATOR live-verify-on-blur flow. */
export async function getBackupRecord(token: string | null, id: number): Promise<BackupRecordRead> {
  return apiFetch<BackupRecordRead>(`/backup-records/${id}`, { token });
}
