import { apiFetch } from "../client";
import type { DailySummary } from "../types";

export async function getDailySummary(token: string | null): Promise<DailySummary> {
  return apiFetch<DailySummary>("/summary/daily", { token });
}
