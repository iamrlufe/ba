import { apiFetch, buildQuery } from "../client";
import type { PaginatedResponse, VerificationRunRead } from "../types";

export interface ListVerificationRunsParams {
  [key: string]: string | number | boolean | undefined;
  status?: string;
  verification_type?: string;
  backup_record_id?: number;
  limit?: number;
  offset?: number;
}

export async function listVerificationRuns(
  token: string | null,
  backupJobId: number,
  params: ListVerificationRunsParams = {},
): Promise<PaginatedResponse<VerificationRunRead>> {
  return apiFetch<PaginatedResponse<VerificationRunRead>>(
    `/backup-jobs/${backupJobId}/verification-runs${buildQuery(params)}`,
    { token },
  );
}

export async function getVerificationRun(
  token: string | null,
  backupJobId: number,
  runId: number,
): Promise<VerificationRunRead> {
  return apiFetch<VerificationRunRead>(`/backup-jobs/${backupJobId}/verification-runs/${runId}`, { token });
}
