import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { FullPageSpinner } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { JobRunStatusBadge } from "@/components/shared/StatusBadge";
import { useAuth } from "@/auth/AuthContext";
import { useJobRunSocket } from "@/hooks/useJobRunSocket";
import { queryKeys } from "@/api/queryKeys";
import { getJobRun, getJobRunLog } from "@/api/endpoints/jobRuns";
import { isJobRunTerminal } from "@/api/types";
import { formatBytes, formatDateTime, formatDuration } from "@/lib/utils";

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const runId = Number(id);
  const { token } = useAuth();

  const runQuery = useQuery({
    queryKey: queryKeys.jobRuns.detail(runId),
    queryFn: () => getJobRun(token, runId),
    enabled: Number.isFinite(runId),
  });

  const run = runQuery.data;
  const isTerminal = run ? isJobRunTerminal(run.status) : false;

  const socket = useJobRunSocket({ jobRunId: runId, token, enabled: Number.isFinite(runId) && !isTerminal });

  const logQuery = useQuery({
    queryKey: queryKeys.jobRuns.log(runId),
    queryFn: () => getJobRunLog(token, runId),
    enabled: isTerminal,
  });

  if (runQuery.isLoading) return <FullPageSpinner />;
  if (runQuery.isError) return <ErrorState error={runQuery.error} onRetry={() => runQuery.refetch()} backTo="/jobs" />;
  if (!run) return null;

  const connectionLabel =
    socket.state === "open"
      ? "live"
      : socket.state === "reconnecting"
        ? `reconnecting… (attempt ${socket.reconnectAttempt})`
        : socket.state === "error"
          ? socket.lastError ?? "connection error"
          : socket.state === "closed"
            ? "disconnected — showing last known state"
            : socket.state === "connecting"
              ? "connecting…"
              : null;

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Run #{run.id}</h1>
          <p className="text-sm text-muted-foreground">Backup job #{run.backup_job_id}</p>
        </div>
        <JobRunStatusBadge status={run.status} />
      </div>

      {connectionLabel && !isTerminal ? (
        <p className="text-xs text-muted-foreground">Connection: {connectionLabel}</p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Progress</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Progress value={run.percent ?? undefined} className={run.percent == null ? "animate-pulse" : undefined} />
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-xs font-medium text-muted-foreground">Percent</p>
              <p>{run.percent != null ? `${run.percent}%` : "unknown"}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Bytes done</p>
              <p>{formatBytes(run.bytes_done)}</p>
            </div>
            <div className="col-span-2">
              <p className="text-xs font-medium text-muted-foreground">Current file</p>
              <p className="break-all">{run.current_file ?? "—"}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 text-sm">
          <Field label="Triggered by">{run.triggered_by}</Field>
          <Field label="Started">{formatDateTime(run.started_at)}</Field>
          <Field label="Finished">{formatDateTime(run.finished_at)}</Field>
          <Field label="Duration">{formatDuration(run.duration_seconds)}</Field>
          <Field label="File path">{run.file_path ?? "—"}</Field>
          <Field label="File size">{formatBytes(run.file_size_bytes)}</Field>
          <Field label="Verification status">
            <Badge variant={run.verification_status === "PASSED" ? "success" : run.verification_status === "FAILED" ? "destructive" : "secondary"}>
              {run.verification_status}
            </Badge>
          </Field>
        </CardContent>
      </Card>

      {run.verification_details ? (
        <Card>
          <CardHeader>
            <CardTitle>Verification details</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">{run.verification_details}</pre>
          </CardContent>
        </Card>
      ) : null}

      {run.error_message ? (
        <Card>
          <CardHeader>
            <CardTitle>Error</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="whitespace-pre-wrap break-all rounded-md bg-destructive/10 p-3 text-xs text-destructive">
              {run.error_message}
            </pre>
          </CardContent>
        </Card>
      ) : null}

      {isTerminal ? (
        <Card>
          <CardHeader>
            <CardTitle>Log</CardTitle>
          </CardHeader>
          <CardContent>
            {logQuery.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading log…</p>
            ) : logQuery.isError ? (
              <ErrorState error={logQuery.error} onRetry={() => logQuery.refetch()} />
            ) : logQuery.data?.log_output ? (
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">
                {logQuery.data.log_output}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">No log output recorded.</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div>{children}</div>
    </div>
  );
}
