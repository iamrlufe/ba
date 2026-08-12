import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { CardSkeleton, TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { WithTooltip } from "@/components/shared/WithTooltip";
import { JobRunStatusBadge, VerificationRunStatusBadge } from "@/components/shared/StatusBadge";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import { deleteBackupJob, getBackupJob, verifyBackupJob } from "@/api/endpoints/backupJobs";
import { listJobRuns, createJobRun } from "@/api/endpoints/jobRuns";
import { listVerificationRuns } from "@/api/endpoints/verificationRuns";
import { ApiError } from "@/api/client";
import { formatDateTime } from "@/lib/utils";
import type { VerificationRunRead, VerificationRunStatus, VerificationType } from "@/api/types";

const ALL = "__all__";

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const jobId = Number(id);
  const { isAdmin, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const jobQuery = useQuery({
    queryKey: queryKeys.backupJobs.detail(jobId),
    queryFn: () => getBackupJob(token, jobId),
    enabled: Number.isFinite(jobId),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteBackupJob(token, jobId),
    onSuccess: () => {
      toast.success("Backup job deleted");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.all() });
      navigate("/jobs");
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to delete backup job");
      setDeleteOpen(false);
    },
  });

  const runNowMutation = useMutation({
    mutationFn: () => createJobRun(token, { backup_job_id: jobId, triggered_by: "manual" }),
    onSuccess: (run) => {
      toast.success("Run started");
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobRuns.all() });
      navigate(`/runs/${run.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to start run");
    },
  });

  const verifyNowMutation = useMutation({
    mutationFn: () => verifyBackupJob(token, jobId),
    onSuccess: () => {
      toast.success("Verification started");
      void queryClient.invalidateQueries({ queryKey: queryKeys.verificationRuns.all(jobId) });
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to start verification");
    },
  });

  if (jobQuery.isLoading) return <CardSkeleton />;
  if (jobQuery.isError) return <ErrorState error={jobQuery.error} onRetry={() => jobQuery.refetch()} backTo="/jobs" />;
  const job = jobQuery.data;
  if (!job) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{job.name}</h1>
          <p className="text-sm text-muted-foreground font-mono">
            {job.trigger_mode === "WATCH" ? `Watch: ${job.watch_directory}` : job.schedule_cron}
          </p>
        </div>
        <div className="flex gap-2">
          {isAdmin ? (
            <>
              <WithTooltip content={job.sql_instance_id == null ? "Verification is not enabled for this job" : null}>
                <Button variant="outline" disabled={job.sql_instance_id == null || verifyNowMutation.isPending} onClick={() => verifyNowMutation.mutate()}>
                  Verify now
                </Button>
              </WithTooltip>
              {job.trigger_mode === "WATCH" ? null : (
                <Button variant="outline" disabled={!job.is_enabled || runNowMutation.isPending} onClick={() => runNowMutation.mutate()}>
                  Run now
                </Button>
              )}
              <Button variant="outline" onClick={() => navigate(`/jobs/${job.id}/edit`)}>
                Edit
              </Button>
              <Button variant="destructive" onClick={() => setDeleteOpen(true)}>
                Delete
              </Button>
            </>
          ) : null}
        </div>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="runs">Run history</TabsTrigger>
          {isAdmin ? <TabsTrigger value="verification">Verification runs</TabsTrigger> : null}
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardHeader>
              <CardTitle>Overview</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
              <Field label="Server">
                <Link to={`/servers/${job.server_id}`} className="hover:underline">
                  #{job.server_id}
                </Link>
              </Field>
              <Field label="Disk">#{job.disk_id}</Field>
              <Field label="Database">{job.database_name ?? "—"}</Field>
              <Field label="Trigger mode">{job.trigger_mode}</Field>
              {job.trigger_mode === "WATCH" ? (
                <Field label="Watch directory">{job.watch_directory}</Field>
              ) : (
                <Field label="Source path">{job.source_path}</Field>
              )}
              <Field label="Backup type">{job.backup_type}</Field>
              <Field label="Timezone">{job.timezone}</Field>
              <Field label="Copy window">
                {job.copy_window_start_hour != null && job.copy_window_end_hour != null
                  ? `${job.copy_window_start_hour}:00 – ${job.copy_window_end_hour}:00${job.copy_window_weekend_unrestricted ? " (unrestricted weekends)" : ""}`
                  : "Unrestricted"}
              </Field>
              <Field label="Retention days">{job.retention_days}</Field>
              <Field label="Retention min copies">{job.retention_min_copies}</Field>
              <Field label="Expected max duration">
                {job.expected_max_duration_minutes != null ? `${job.expected_max_duration_minutes} min` : "—"}
              </Field>
              <Field label="Missed-run grace">{job.missed_run_grace_minutes} min</Field>
              <Field label="Verification method">{job.verification_method ?? "—"}</Field>
              <Field label="SQL instance">
                {job.sql_instance_id != null ? `#${job.sql_instance_id}` : "None"}
              </Field>
              <Field label="Local backup path pattern">{job.local_backup_path_pattern ?? "—"}</Field>
              <Field label="Enabled">
                <Badge variant={job.is_enabled ? "success" : "secondary"}>{job.is_enabled ? "Yes" : "No"}</Badge>
              </Field>
              <Field label="Last run">{formatDateTime(job.last_run_at)}</Field>
              <Field label="Next run">{formatDateTime(job.next_run_at)}</Field>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="runs">
          <RunHistoryTab jobId={jobId} />
        </TabsContent>

        {isAdmin ? (
          <TabsContent value="verification">
            <VerificationRunsTab jobId={jobId} />
          </TabsContent>
        ) : null}
      </Tabs>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete backup job?"
        description={`This will permanently delete "${job.name}". This action cannot be undone.`}
        destructive
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
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

function RunHistoryTab({ jobId }: { jobId: number }) {
  const { token } = useAuth();
  const query = usePaginatedQuery({
    queryKey: queryKeys.jobRuns.list({ backup_job_id: jobId }),
    queryFn: ({ limit, offset }) => listJobRuns(token, { backup_job_id: jobId, limit, offset }),
  });

  return (
    <Card>
      <CardContent className="pt-6">
        {query.isLoading ? (
          <TableSkeleton />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.data && query.data.items.length === 0 ? (
          <EmptyState title="No runs yet" />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Finished</TableHead>
                  <TableHead>Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.data?.items.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>
                      <Link to={`/runs/${run.id}`} className="hover:underline">
                        #{run.id}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <JobRunStatusBadge status={run.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(run.started_at)}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(run.finished_at)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {run.duration_seconds != null ? `${run.duration_seconds}s` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <PaginationBar total={query.data?.total ?? 0} limit={query.limit} offset={query.offset} onOffsetChange={query.setOffset} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function VerificationRunsTab({ jobId }: { jobId: number }) {
  const { token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const status = (searchParams.get("v_status") as VerificationRunStatus | null) ?? undefined;
  const verificationType = (searchParams.get("v_type") as VerificationType | null) ?? undefined;

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL) next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const query = usePaginatedQuery({
    queryKey: queryKeys.verificationRuns.list(jobId, { status, verificationType }),
    queryFn: ({ limit, offset }) =>
      listVerificationRuns(token, jobId, { status, verification_type: verificationType, limit, offset }),
  });

  const [detailRun, setDetailRun] = useState<VerificationRunRead | null>(null);

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <Select value={status ?? ALL} onValueChange={(v) => updateFilter("v_status", v)}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {(["PENDING", "RUNNING", "OK", "CORRUPT", "MISSING", "ERROR"] as VerificationRunStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={verificationType ?? ALL} onValueChange={(v) => updateFilter("v_type", v)}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            {(["RESTORE_VERIFYONLY", "FTP_COPY_INTEGRITY"] as VerificationType[]).map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="pt-6">
          {query.isLoading ? (
            <TableSkeleton />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState title="No verification runs" />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Run</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Finished</TableHead>
                    <TableHead>Error</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((run) => (
                    <TableRow
                      key={run.id}
                      className="cursor-pointer"
                      onClick={() => setDetailRun(run)}
                    >
                      <TableCell>#{run.id}</TableCell>
                      <TableCell className="text-muted-foreground">{run.verification_type}</TableCell>
                      <TableCell>
                        <VerificationRunStatusBadge status={run.status} />
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(run.started_at)}</TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(run.finished_at)}</TableCell>
                      <TableCell className="max-w-xs truncate text-muted-foreground" title={run.error_message ?? undefined}>
                        {run.error_message ?? "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationBar total={query.data?.total ?? 0} limit={query.limit} offset={query.offset} onOffsetChange={query.setOffset} />
            </>
          )}
        </CardContent>
      </Card>

      <VerificationRunDetailDialog run={detailRun} onOpenChange={(open) => !open && setDetailRun(null)} />
    </div>
  );
}

/**
 * Full detail for a single verification run -- in particular
 * `verifyonly_output` (the actual RESTORE VERIFYONLY server output) and
 * `msdb_backup_date`/`msdb_is_damaged`, none of which are shown in the
 * summary table. Without this, an admin investigating a CORRUPT/ERROR run
 * has no way to see the evidence explaining why.
 */
function VerificationRunDetailDialog({
  run,
  onOpenChange,
}: {
  run: VerificationRunRead | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={run !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Verification run #{run?.id}</DialogTitle>
        </DialogHeader>
        {run ? (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <DetailField label="Type">{run.verification_type}</DetailField>
              <DetailField label="Status">
                <VerificationRunStatusBadge status={run.status} />
              </DetailField>
              <DetailField label="Triggered by">{run.triggered_by}</DetailField>
              <DetailField label="Started">{formatDateTime(run.started_at)}</DetailField>
              <DetailField label="Finished">{formatDateTime(run.finished_at)}</DetailField>
              <DetailField label="msdb backup date">{formatDateTime(run.msdb_backup_date)}</DetailField>
              <DetailField label="msdb is_damaged">
                {run.msdb_is_damaged === null ? "—" : run.msdb_is_damaged ? "Yes" : "No"}
              </DetailField>
            </div>
            {run.error_message ? (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">Error message</p>
                <pre className="max-h-40 overflow-auto rounded-md border bg-muted/40 p-2 text-xs whitespace-pre-wrap">
                  {run.error_message}
                </pre>
              </div>
            ) : null}
            {run.verifyonly_output ? (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">Verification output</p>
                <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-2 text-xs whitespace-pre-wrap">
                  {run.verifyonly_output}
                </pre>
              </div>
            ) : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function DetailField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div>{children}</div>
    </div>
  );
}
