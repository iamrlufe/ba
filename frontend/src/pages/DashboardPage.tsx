import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { AlertSeverityBadge } from "@/components/shared/StatusBadge";
import { useAuth } from "@/auth/AuthContext";
import { queryKeys } from "@/api/queryKeys";
import { getDailySummary } from "@/api/endpoints/summary";
import { listServers } from "@/api/endpoints/servers";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { acknowledgeAlert, resolveAlert } from "@/api/endpoints/alerts";
import { ApiError } from "@/api/client";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";
import type { AlertRead, DailySummary } from "@/api/types";

function dailyJobStatusVariant(status: string): "success" | "destructive" | "warning" {
  if (status === "OK") return "success";
  if (status === "MISSED") return "warning";
  return "destructive";
}

export function DashboardPage() {
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();

  const summaryQuery = useQuery({
    queryKey: queryKeys.summary.daily(),
    queryFn: () => getDailySummary(token),
  });

  const serversQuery = useQuery({
    queryKey: queryKeys.servers.list({}),
    queryFn: () => listServers(token, {}),
  });

  const upcomingJobsQuery = useQuery({
    queryKey: queryKeys.backupJobs.list({ is_enabled: true }),
    queryFn: () => listBackupJobs(token, { is_enabled: true, limit: 200 }),
  });

  const ackMutation = useMutation({
    mutationFn: (alertId: number) => acknowledgeAlert(token, alertId),
    onMutate: async (alertId) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.summary.daily() });
      const previous = queryClient.getQueryData<DailySummary>(queryKeys.summary.daily());
      if (previous) {
        queryClient.setQueryData<DailySummary>(queryKeys.summary.daily(), {
          ...previous,
          active_alerts: previous.active_alerts.map((a) =>
            a.id === alertId ? { ...a, status: "ACKNOWLEDGED" as const } : a,
          ),
        });
      }
      return { previous };
    },
    onError: (error, _alertId, context) => {
      if (context?.previous) queryClient.setQueryData(queryKeys.summary.daily(), context.previous);
      toast.error(error instanceof ApiError ? error.detail : "Failed to acknowledge alert");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.summary.daily() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: (alertId: number) => resolveAlert(token, alertId, {}),
    onSuccess: () => {
      toast.success("Alert resolved");
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to resolve alert");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.summary.daily() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
    },
  });

  const summary = summaryQuery.data;

  const onlineCount = serversQuery.data?.items.filter((s) => s.status === "ACTIVE").length ?? 0;
  const offlineCount = serversQuery.data?.items.filter((s) => s.status === "OFFLINE" || s.status === "UNREACHABLE").length ?? 0;

  const upcoming = [...(upcomingJobsQuery.data?.items ?? [])]
    .filter((j) => j.next_run_at != null)
    .sort((a, b) => new Date(a.next_run_at as string).getTime() - new Date(b.next_run_at as string).getTime())
    .slice(0, 8);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <KpiCard label="Jobs OK" value={summary?.counts.jobs_ok} loading={summaryQuery.isLoading} />
        <KpiCard label="Jobs failed" value={summary?.counts.jobs_fail} loading={summaryQuery.isLoading} tone="destructive" />
        <KpiCard label="Jobs missed" value={summary?.counts.jobs_missed} loading={summaryQuery.isLoading} tone="warning" />
        <KpiCard label="Active alerts" value={summary?.counts.active_alerts_total} loading={summaryQuery.isLoading} tone="destructive" />
        <KpiCard
          label="Servers online / offline"
          value={serversQuery.isLoading ? undefined : `${onlineCount} / ${offlineCount}`}
          loading={serversQuery.isLoading}
        />
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Active alerts</CardTitle>
        </CardHeader>
        <CardContent>
          {summaryQuery.isLoading ? (
            <TableSkeleton rows={3} columns={5} />
          ) : summaryQuery.isError ? (
            <ErrorState error={summaryQuery.error} onRetry={() => summaryQuery.refetch()} />
          ) : summary && summary.active_alerts.length === 0 ? (
            <EmptyState title="No active alerts" description="Everything looks healthy." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Severity</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Created</TableHead>
                  {isAdmin ? <TableHead className="text-right">Actions</TableHead> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {summary?.active_alerts.map((alert: AlertRead) => (
                  <TableRow key={alert.id}>
                    <TableCell>
                      <AlertSeverityBadge severity={alert.severity} />
                    </TableCell>
                    <TableCell>{alert.title}</TableCell>
                    <TableCell className="text-muted-foreground">{alert.alert_type}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(alert.created_at)}</TableCell>
                    {isAdmin ? (
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={ackMutation.isPending || alert.status === "ACKNOWLEDGED"}
                            onClick={() => ackMutation.mutate(alert.id)}
                          >
                            Acknowledge
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={resolveMutation.isPending}
                            onClick={() => resolveMutation.mutate(alert.id)}
                          >
                            Resolve
                          </Button>
                        </div>
                      </TableCell>
                    ) : null}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Job status (today)</CardTitle>
          </CardHeader>
          <CardContent>
            {summaryQuery.isLoading ? (
              <TableSkeleton rows={4} columns={3} />
            ) : summaryQuery.isError ? (
              <ErrorState error={summaryQuery.error} onRetry={() => summaryQuery.refetch()} />
            ) : summary && summary.jobs.length === 0 ? (
              <EmptyState title="No backup jobs yet" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Job</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last run</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {summary?.jobs.map((job) => (
                    <TableRow key={job.backup_job_id}>
                      <TableCell>
                        <Link to={`/jobs/${job.backup_job_id}`} className="hover:underline">
                          {job.name}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant={dailyJobStatusVariant(job.status)}>{job.status}</Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatDateTime(job.last_run_finished_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Upcoming scheduled runs</CardTitle>
          </CardHeader>
          <CardContent>
            {upcomingJobsQuery.isLoading ? (
              <TableSkeleton rows={4} columns={2} />
            ) : upcomingJobsQuery.isError ? (
              <ErrorState error={upcomingJobsQuery.error} onRetry={() => upcomingJobsQuery.refetch()} />
            ) : upcoming.length === 0 ? (
              <EmptyState title="Nothing scheduled" description="No enabled jobs have a known next run time." />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Job</TableHead>
                    <TableHead>Next run</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {upcoming.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell>
                        <Link to={`/jobs/${job.id}`} className="hover:underline">
                          {job.name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatRelativeTime(job.next_run_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  loading,
  tone,
}: {
  label: string;
  value: number | string | undefined;
  loading: boolean;
  tone?: "destructive" | "warning";
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p
          className={
            "text-2xl font-bold " +
            (tone === "destructive" ? "text-destructive" : tone === "warning" ? "text-warning" : "")
          }
        >
          {loading ? "…" : (value ?? "—")}
        </p>
      </CardContent>
    </Card>
  );
}
