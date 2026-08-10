import { apiFetch, buildQuery } from "../client";
import type { AlertRead, AlertResolveRequest, PaginatedResponse } from "../types";

export interface ListAlertsParams {
  [key: string]: string | number | boolean | undefined;
  status?: string;
  severity?: string;
  alert_type?: string;
  limit?: number;
  offset?: number;
}

export async function listAlerts(
  token: string | null,
  params: ListAlertsParams = {},
): Promise<PaginatedResponse<AlertRead>> {
  return apiFetch<PaginatedResponse<AlertRead>>(`/alerts${buildQuery(params)}`, { token });
}

/** ADMIN-only. */
export async function acknowledgeAlert(token: string | null, id: number): Promise<AlertRead> {
  return apiFetch<AlertRead>(`/alerts/${id}/acknowledge`, { method: "POST", body: {}, token });
}

/** ADMIN-only. */
export async function resolveAlert(
  token: string | null,
  id: number,
  payload: AlertResolveRequest,
): Promise<AlertRead> {
  return apiFetch<AlertRead>(`/alerts/${id}/resolve`, { method: "POST", body: payload, token });
}
