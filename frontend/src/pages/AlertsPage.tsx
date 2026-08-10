import { Fragment, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { AlertSeverityBadge, AlertStatusBadge } from "@/components/shared/StatusBadge";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import { acknowledgeAlert, listAlerts, resolveAlert } from "@/api/endpoints/alerts";
import { ApiError } from "@/api/client";
import { formatDateTime } from "@/lib/utils";
import type { AlertRead, AlertSeverity, AlertStatus, AlertType } from "@/api/types";

const ALL = "__all__";

const entityLinks: Array<{ key: keyof AlertRead; label: string; to: (id: number) => string }> = [
  { key: "server_id", label: "Server", to: (id) => `/servers/${id}` },
  { key: "backup_job_id", label: "Backup job", to: (id) => `/jobs/${id}` },
  { key: "job_run_id", label: "Job run", to: (id) => `/runs/${id}` },
  { key: "restore_operation_id", label: "Restore operation", to: () => `/restore` },
];

export function AlertsPage() {
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [resolveTarget, setResolveTarget] = useState<AlertRead | null>(null);
  const [resolveNote, setResolveNote] = useState("");

  const status = (searchParams.get("status") as AlertStatus | null) ?? undefined;
  const severity = (searchParams.get("severity") as AlertSeverity | null) ?? undefined;
  const alertType = (searchParams.get("alert_type") as AlertType | null) ?? undefined;

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL) next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const query = usePaginatedQuery({
    queryKey: queryKeys.alerts.list({ status, severity, alertType }),
    queryFn: ({ limit, offset }) => listAlerts(token, { status, severity, alert_type: alertType, limit, offset }),
  });

  const ackMutation = useMutation({
    mutationFn: (alertId: number) => acknowledgeAlert(token, alertId),
    onSuccess: () => toast.success("Alert acknowledged"),
    onError: (error) => toast.error(error instanceof ApiError ? error.detail : "Failed to acknowledge alert"),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.summary.daily() });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({ alertId, note }: { alertId: number; note: string }) =>
      resolveAlert(token, alertId, { resolved_note: note || null }),
    onSuccess: () => {
      toast.success("Alert resolved");
      setResolveTarget(null);
      setResolveNote("");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.detail : "Failed to resolve alert"),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts.all() });
      void queryClient.invalidateQueries({ queryKey: queryKeys.summary.daily() });
    },
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Alerts</h1>

      <div className="flex flex-wrap gap-3">
        <Select value={status ?? ALL} onValueChange={(v) => updateFilter("status", v)}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {(["ACTIVE", "ACKNOWLEDGED", "RESOLVED"] as AlertStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={severity ?? ALL} onValueChange={(v) => updateFilter("severity", v)}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All severities" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All severities</SelectItem>
            {(["INFO", "WARNING", "CRITICAL"] as AlertSeverity[]).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={alertType ?? ALL} onValueChange={(v) => updateFilter("alert_type", v)}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            {(
              [
                "JOB_FAILED",
                "JOB_MISSED",
                "JOB_TIMEOUT",
                "VERIFICATION_FAILED",
                "BACKUP_VERIFICATION_FAILED",
                "FTP_COPY_INTEGRITY_FAILED",
                "DISK_SPACE_LOW",
                "DISK_SPACE_CRITICAL",
                "SERVER_UNREACHABLE",
                "AGENT_OFFLINE",
                "RESTORE_FAILED",
              ] as AlertType[]
            ).map((t) => (
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
            <EmptyState title="No alerts" description="Nothing matches these filters." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead />
                    <TableHead>Severity</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Created</TableHead>
                    {isAdmin ? <TableHead className="text-right">Actions</TableHead> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((alert) => (
                    <Fragment key={alert.id}>
                      <TableRow className="cursor-pointer" onClick={() => setExpandedId(expandedId === alert.id ? null : alert.id)}>
                        <TableCell>
                          {expandedId === alert.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </TableCell>
                        <TableCell>
                          <AlertSeverityBadge severity={alert.severity} />
                        </TableCell>
                        <TableCell>
                          <AlertStatusBadge status={alert.status} />
                        </TableCell>
                        <TableCell>{alert.title}</TableCell>
                        <TableCell className="text-muted-foreground">{alert.alert_type}</TableCell>
                        <TableCell className="text-muted-foreground">{formatDateTime(alert.created_at)}</TableCell>
                        {isAdmin ? (
                          <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                            <div className="flex justify-end gap-2">
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={ackMutation.isPending || alert.status !== "ACTIVE"}
                                onClick={() => ackMutation.mutate(alert.id)}
                              >
                                Acknowledge
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={resolveMutation.isPending || alert.status === "RESOLVED"}
                                onClick={() => setResolveTarget(alert)}
                              >
                                Resolve
                              </Button>
                            </div>
                          </TableCell>
                        ) : null}
                      </TableRow>
                      {expandedId === alert.id ? (
                        <TableRow>
                          <TableCell colSpan={isAdmin ? 7 : 6} className="bg-muted/30">
                            <div className="space-y-2 p-2 text-sm">
                              <p>{alert.message}</p>
                              {alert.resolved_note ? (
                                <p className="text-muted-foreground">Resolution note: {alert.resolved_note}</p>
                              ) : null}
                              <div className="flex flex-wrap gap-3">
                                {entityLinks.map(({ key, label, to }) => {
                                  const value = alert[key];
                                  if (typeof value !== "number") return null;
                                  return (
                                    <Link key={key} to={to(value)} className="text-primary hover:underline">
                                      {label} #{value}
                                    </Link>
                                  );
                                })}
                                {alert.disk_id != null ? <span className="text-muted-foreground">Disk #{alert.disk_id}</span> : null}
                                {alert.backup_record_id != null ? (
                                  <span className="text-muted-foreground">Backup record #{alert.backup_record_id}</span>
                                ) : null}
                              </div>
                            </div>
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
              <PaginationBar total={query.data?.total ?? 0} limit={query.limit} offset={query.offset} onOffsetChange={query.setOffset} />
            </>
          )}
        </CardContent>
      </Card>

      <Dialog open={resolveTarget != null} onOpenChange={(open) => !open && setResolveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Resolve alert</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">{resolveTarget?.title}</p>
            <Textarea
              placeholder="Optional resolution note"
              value={resolveNote}
              onChange={(e) => setResolveNote(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={resolveMutation.isPending}
              onClick={() => resolveTarget && resolveMutation.mutate({ alertId: resolveTarget.id, note: resolveNote })}
            >
              {resolveMutation.isPending ? "Resolving…" : "Resolve"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
