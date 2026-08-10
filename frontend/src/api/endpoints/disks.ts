import { apiFetch, buildQuery } from "../client";
import type { DiskCreate, DiskRead, DiskUpdate, PaginatedResponse } from "../types";

export interface ListDisksParams {
  [key: string]: string | number | boolean | undefined;
  server_id?: number;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}

export async function listDisks(
  token: string | null,
  params: ListDisksParams = {},
): Promise<PaginatedResponse<DiskRead>> {
  return apiFetch<PaginatedResponse<DiskRead>>(`/disks${buildQuery(params)}`, { token });
}

export async function getDisk(token: string | null, id: number): Promise<DiskRead> {
  return apiFetch<DiskRead>(`/disks/${id}`, { token });
}

export async function createDisk(token: string | null, payload: DiskCreate): Promise<DiskRead> {
  return apiFetch<DiskRead>("/disks", { method: "POST", body: payload, token });
}

export async function updateDisk(token: string | null, id: number, payload: DiskUpdate): Promise<DiskRead> {
  return apiFetch<DiskRead>(`/disks/${id}`, { method: "PATCH", body: payload, token });
}

export async function deleteDisk(token: string | null, id: number): Promise<void> {
  await apiFetch<void>(`/disks/${id}`, { method: "DELETE", token });
}
