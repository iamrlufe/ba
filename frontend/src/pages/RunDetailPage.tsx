import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FullPageSpinner } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { JobRunStatusBadge } from "@/components/shared/StatusBadge";
import { useAuth } from "@/auth/AuthContext";
import { useJobRunSocket } from "@/hooks/useJobRunSocket";
import { queryKeys } from "@/api/queryKeys";
import { cancelJobRun, getJobRun, getJobRunLog } from "@/api/endpoints/jobRuns";
import { isJobRunTerminal } from "@/api/types";
import { ApiError } from "@/api/client";
import { formatBytes, formatDateTime, formatDuration } from "@/lib/utils";

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const runId = Number(id);
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();
  const [cancelOpen, setCancelOpen] = useState(false);

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

  const cancelMutation = useMutation({
    mutationFn: () => cancelJobRun(token, runId),
    onSuccess: (updatedRun) => {
      toast.success("Run cancelled");
      queryClient.setQueryData(queryKeys.jobRuns.detail(runId), updatedRun);
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobRuns.all() });
      setCancelOpen(false);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        toast.error("This run already finished and can't be cancelled.");
        void queryClient.invalidateQueries({ queryKey: queryKeys.jobRuns.detail(runId) });
      } else {
        toast.error(error instanceof ApiError ? error.detail : "Failed to cancel run");
      }
      setCancelOpen(false);
    },
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
        <div className="flex items-center gap-3">
          <JobRunStatusBadge status={run.status} />
          {isAdmin && !isTerminal ? (
            <Button variant="destructive" onClick={() => setCancelOpen(true)}>
              Cancel run
            </Button>
          ) : null}
        </div>
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

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Cancel this run?"
        description="The agent will stop copying immediately. This cannot be undone -- trigger a new run afterward if the backup is still needed."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        destructive
        isConfirming={cancelMutation.isPending}
        onConfirm={() => cancelMutation.mutate()}
      />
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
