/**
 * Hand-curated, ergonomic re-exports on top of the generated `schema.gen.ts`.
 *
 * Do NOT hand-write wire types here from scratch -- every interface below is
 * a straight alias of `components['schemas'][...]` from the generated file,
 * which itself is generated from the backend's live `/openapi.json`
 * (see `npm run gen:api`). If a backend schema changes, regenerate rather
 * than hand-editing shapes here.
 */
import type { components } from "./schema.gen";

// ---------------------------------------------------------------------------
// Enums (string literal unions) -- mirrors app/models/enums.py exactly.
// ---------------------------------------------------------------------------

export type ProtocolType = components["schemas"]["ProtocolType"];
export type ServerStatus = components["schemas"]["ServerStatus"];
export type BackupType = components["schemas"]["BackupType"];
export type JobRunStatus = components["schemas"]["JobRunStatus"];
export type VerificationStatus = components["schemas"]["VerificationStatus"];
export type AlertType = components["schemas"]["AlertType"];
export type AlertSeverity = components["schemas"]["AlertSeverity"];
export type AlertStatus = components["schemas"]["AlertStatus"];
export type AlertChannel = components["schemas"]["AlertChannel"];
export type RestoreMode = components["schemas"]["RestoreMode"];
export type RestoreStatus = components["schemas"]["RestoreStatus"];
export type RequestChannel = components["schemas"]["RequestChannel"];
export type VerificationRunStatus = components["schemas"]["VerificationRunStatus"];
export type UserRole = components["schemas"]["UserRole"];
export type VerificationType = components["schemas"]["VerificationType"];
export type AgentCopyVerificationStatus = components["schemas"]["AgentCopyVerificationStatus"];

// ---------------------------------------------------------------------------
// Terminal-status constants -- mirrors app/models/enums.py's frozensets.
// ---------------------------------------------------------------------------

export const JOB_RUN_TERMINAL_STATUSES: readonly JobRunStatus[] = [
  "SUCCESS",
  "FAILED",
  "WARNING",
  "CANCELLED",
  "TIMEOUT",
];

export const RESTORE_TERMINAL_STATUSES: readonly RestoreStatus[] = ["DONE", "FAILED", "CANCELLED"];

export const VERIFICATION_RUN_TERMINAL_STATUSES: readonly VerificationRunStatus[] = [
  "OK",
  "CORRUPT",
  "MISSING",
  "ERROR",
];

export function isJobRunTerminal(status: JobRunStatus): boolean {
  return (JOB_RUN_TERMINAL_STATUSES as readonly string[]).includes(status);
}

export function isRestoreTerminal(status: RestoreStatus): boolean {
  return (RESTORE_TERMINAL_STATUSES as readonly string[]).includes(status);
}

export function isVerificationRunTerminal(status: VerificationRunStatus): boolean {
  return (VERIFICATION_RUN_TERMINAL_STATUSES as readonly string[]).includes(status);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export type LoginRequest = components["schemas"]["LoginRequest"];
export type LoginResponse = components["schemas"]["LoginResponse"];
export type UserRead = components["schemas"]["MeResponse"];

// ---------------------------------------------------------------------------
// Servers / Disks
// ---------------------------------------------------------------------------

export type ServerRead = components["schemas"]["ServerRead"];
export type ServerCreate = components["schemas"]["ServerCreate"];
export type ServerUpdate = components["schemas"]["ServerUpdate"];

export type DiskRead = components["schemas"]["DiskRead"];
export type DiskCreate = components["schemas"]["DiskCreate"];
export type DiskUpdate = components["schemas"]["DiskUpdate"];

// ---------------------------------------------------------------------------
// Backup jobs
// ---------------------------------------------------------------------------

export type BackupJobRead = components["schemas"]["BackupJobRead"];
export type BackupJobCreate = components["schemas"]["BackupJobCreate"];
export type BackupJobUpdate = components["schemas"]["BackupJobUpdate"];

// ---------------------------------------------------------------------------
// SQL instances
// ---------------------------------------------------------------------------

export type SqlInstanceRead = components["schemas"]["SqlInstanceRead"];
export type SqlInstanceCreate = components["schemas"]["SqlInstanceCreate"];
export type SqlInstanceUpdate = components["schemas"]["SqlInstanceUpdate"];

// ---------------------------------------------------------------------------
// Job runs
// ---------------------------------------------------------------------------

export type JobRunRead = components["schemas"]["JobRunRead"];
export type JobRunLogRead = components["schemas"]["JobRunLogRead"];
export type JobRunCreate = components["schemas"]["JobRunCreate"];
export type JobRunUpdate = components["schemas"]["JobRunUpdate"];
export type JobRunCompleteRequest = components["schemas"]["JobRunCompleteRequest"];

// ---------------------------------------------------------------------------
// Verification runs
// ---------------------------------------------------------------------------

export type VerificationRunRead = components["schemas"]["VerificationRunRead"];

// ---------------------------------------------------------------------------
// Backup records
// ---------------------------------------------------------------------------

export type BackupRecordRead = components["schemas"]["BackupRecordRead"];
export type BackupRecordCreate = components["schemas"]["BackupRecordCreate"];

// ---------------------------------------------------------------------------
// Restore operations
// ---------------------------------------------------------------------------

export type RestoreOperationRead = components["schemas"]["RestoreOperationRead"];
export type RestoreOperationCreate = components["schemas"]["RestoreOperationCreate"];
export type RestoreOperationUpdate = components["schemas"]["RestoreOperationUpdate"];
export type RestoreOperationLogRead = components["schemas"]["RestoreOperationLogRead"];

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

export type AlertRead = components["schemas"]["AlertRead"];
export type AlertResolveRequest = components["schemas"]["AlertResolveRequest"];
export type AlertAcknowledgeRequest = components["schemas"]["AlertAcknowledgeRequest"];

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

export type DailySummary = components["schemas"]["DailySummary"];
export type DailyJobStatus = components["schemas"]["DailyJobStatus"];
export type DailyJobStatusValue = DailyJobStatus["status"];
export type DailySummaryCounts = components["schemas"]["DailySummaryCounts"];

// ---------------------------------------------------------------------------
// Generic / shared
// ---------------------------------------------------------------------------

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  detail: string;
}
