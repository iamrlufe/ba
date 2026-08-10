import { Badge } from "@/components/ui/badge";
import {
  alertSeverityVariant,
  alertStatusVariant,
  jobRunStatusVariant,
  restoreStatusVariant,
  serverStatusVariant,
  verificationRunStatusVariant,
} from "@/lib/statusStyles";
import type {
  AlertSeverity,
  AlertStatus,
  JobRunStatus,
  RestoreStatus,
  ServerStatus,
  VerificationRunStatus,
} from "@/api/types";

export function JobRunStatusBadge({ status }: { status: JobRunStatus }) {
  return <Badge variant={jobRunStatusVariant[status]}>{status}</Badge>;
}

export function VerificationRunStatusBadge({ status }: { status: VerificationRunStatus }) {
  return <Badge variant={verificationRunStatusVariant[status]}>{status}</Badge>;
}

export function RestoreStatusBadge({ status }: { status: RestoreStatus }) {
  return <Badge variant={restoreStatusVariant[status]}>{status}</Badge>;
}

export function AlertSeverityBadge({ severity }: { severity: AlertSeverity }) {
  return <Badge variant={alertSeverityVariant[severity]}>{severity}</Badge>;
}

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  return <Badge variant={alertStatusVariant[status]}>{status}</Badge>;
}

export function ServerStatusBadge({ status }: { status: ServerStatus }) {
  return <Badge variant={serverStatusVariant[status]}>{status}</Badge>;
}
