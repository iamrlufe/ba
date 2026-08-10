import { apiFetch, buildQuery } from "../client";
import type { JobRunCreate, JobRunLogRead, JobRunRead, PaginatedResponse } from "../types";

export interface ListJobRunsParams {
  [key: string]: string | number | boolean | undefined;
  backup_job_id?: number;
  status?: string;
  limit?: number;
  offset?: number;
}

export async function listJobRuns(
  token: string | null,
  params: ListJobRunsParams = {},
): Promise<PaginatedResponse<JobRunRead>> {
  return apiFetch<PaginatedResponse<JobRunRead>>(`/job-runs${buildQuery(params)}`, { token });
}

export async function getJobRun(token: string | null, id: number): Promise<JobRunRead> {
  return apiFetch<JobRunRead>(`/job-runs/${id}`, { token });
}

export async function getJobRunLog(token: string | null, id: number): Promise<JobRunLogRead> {
  return apiFetch<JobRunLogRead>(`/job-runs/${id}/log`, { token });
}

export async function createJobRun(token: string | null, payload: JobRunCreate): Promise<JobRunRead> {
  return apiFetch<JobRunRead>("/job-runs", { method: "POST", body: payload, token });
}
