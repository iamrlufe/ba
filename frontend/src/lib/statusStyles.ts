import type { AlertSeverity, AlertStatus, JobRunStatus, RestoreStatus, ServerStatus, VerificationRunStatus } from "@/api/types";

export type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning" | "info";

export const jobRunStatusVariant: Record<JobRunStatus, BadgeVariant> = {
  PENDING: "secondary",
  CANCELLED: "secondary",
  RUNNING: "info",
  SUCCESS: "success",
  WARNING: "warning",
  FAILED: "destructive",
  TIMEOUT: "destructive",
  STUCK: "destructive",
};

export const verificationRunStatusVariant: Record<VerificationRunStatus, BadgeVariant> = {
  PENDING: "secondary",
  RUNNING: "info",
  OK: "success",
  CORRUPT: "destructive",
  MISSING: "destructive",
  ERROR: "destructive",
};

export const restoreStatusVariant: Record<RestoreStatus, BadgeVariant> = {
  PENDING: "secondary",
  CANCELLED: "secondary",
  RUNNING: "info",
  DONE: "success",
  FAILED: "destructive",
};

export const alertSeverityVariant: Record<AlertSeverity, BadgeVariant> = {
  INFO: "info",
  WARNING: "warning",
  CRITICAL: "destructive",
};

export const alertStatusVariant: Record<AlertStatus, BadgeVariant> = {
  ACTIVE: "destructive",
  ACKNOWLEDGED: "warning",
  RESOLVED: "secondary",
};

export const serverStatusVariant: Record<ServerStatus, BadgeVariant> = {
  ACTIVE: "success",
  DISABLED: "secondary",
  UNREACHABLE: "warning",
  OFFLINE: "destructive",
};
