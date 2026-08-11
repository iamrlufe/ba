import { apiFetch, buildQuery } from "../client";
import type { PaginatedResponse, ServerCreate, ServerMetricsResponse, ServerRead, ServerUpdate } from "../types";

export interface ListServersParams {
  [key: string]: string | number | boolean | undefined;
  status?: string;
  include_deleted?: boolean;
  limit?: number;
  offset?: number;
}

export async function listServers(
  token: string | null,
  params: ListServersParams = {},
): Promise<PaginatedResponse<ServerRead>> {
  return apiFetch<PaginatedResponse<ServerRead>>(`/servers${buildQuery(params)}`, { token });
}

export async function getServer(token: string | null, id: number): Promise<ServerRead> {
  return apiFetch<ServerRead>(`/servers/${id}`, { token });
}

export async function createServer(token: string | null, payload: ServerCreate): Promise<ServerRead> {
  return apiFetch<ServerRead>("/servers", { method: "POST", body: payload, token });
}

export async function updateServer(token: string | null, id: number, payload: ServerUpdate): Promise<ServerRead> {
  return apiFetch<ServerRead>(`/servers/${id}`, { method: "PATCH", body: payload, token });
}

export async function deleteServer(token: string | null, id: number): Promise<void> {
  await apiFetch<void>(`/servers/${id}`, { method: "DELETE", token });
}

export async function getServerMetrics(token: string | null, id: number): Promise<ServerMetricsResponse> {
  return apiFetch<ServerMetricsResponse>(`/servers/${id}/metrics`, { token });
}
