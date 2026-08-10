import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { RestoreStatusBadge } from "@/components/shared/StatusBadge";
import { WithTooltip } from "@/components/shared/WithTooltip";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import {
  createRestoreOperation,
  getRestoreOperationLog,
  listRestoreOperations,
  updateRestoreOperation,
} from "@/api/endpoints/restoreOperations";
import { listSqlInstances } from "@/api/endpoints/sqlInstances";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { listBackupRecords } from "@/api/endpoints/backupRecords";
import { getBackupRecord } from "@/api/endpoints/backupRecords";
import { ApiError } from "@/api/client";
import { formatDateTime } from "@/lib/utils";
import type { BackupRecordRead, RestoreMode, RestoreStatus } from "@/api/types";

const ALL = "__all__";

export function RestorePage() {
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [logTarget, setLogTarget] = useState<number | null>(null);

  const status = (searchParams.get("status") as RestoreStatus | null) ?? undefined;
  const sqlInstanceIdParam = searchParams.get("sql_instance_id");
  const sqlInstanceId = sqlInstanceIdParam ? Number(sqlInstanceIdParam) : undefined;
  const backupRecordIdParam = searchParams.get("backup_record_id");
  const backupRecordId = backupRecordIdParam ? Number(backupRecordIdParam) : undefined;

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL || value === "") next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const sqlInstancesQuery = useQuery({
    queryKey: queryKeys.sqlInstances.list({}),
    queryFn: () => listSqlInstances(token, { limit: 200 }),
  });

  const query = usePaginatedQuery({
    queryKey: queryKeys.restoreOperations.list({ status, sqlInstanceId, backupRecordId }),
    queryFn: ({ limit, offset }) =>
      listRestoreOperations(token, { status, sql_instance_id: sqlInstanceId, backup_record_id: backupRecordId, limit, offset }),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: number) => updateRestoreOperation(token, id, { status: "CANCELLED" }),
    onSuccess: () => {
      toast.success("Restore operation cancelled");
      void queryClient.invalidateQueries({ queryKey: queryKeys.restoreOperations.all() });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.detail : "Failed to cancel restore operation"),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Restore</h1>

      <RestoreCreateForm />

      <Card>
        <CardHeader>
          <CardTitle>History</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-3">
            <Select value={status ?? ALL} onValueChange={(v) => updateFilter("status", v)}>
              <SelectTrigger className="w-40">
                <SelectValue placeholder="All statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All statuses</SelectItem>
                {(["PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED"] as RestoreStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sqlInstanceIdParam ?? ALL} onValueChange={(v) => updateFilter("sql_instance_id", v)}>
              <SelectTrigger className="w-56">
                <SelectValue placeholder="All SQL instances" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All SQL instances</SelectItem>
                {sqlInstancesQuery.data?.items.map((si) => (
                  <SelectItem key={si.id} value={String(si.id)}>
                    {si.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              className="w-56"
              placeholder="Backup record ID"
              type="number"
              defaultValue={backupRecordIdParam ?? ""}
              onBlur={(e) => updateFilter("backup_record_id", e.target.value)}
            />
          </div>

          {query.isLoading ? (
            <TableSkeleton />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState title="No restore operations" description="Nothing matches these filters." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead>
                    <TableHead>Database</TableHead>
                    <TableHead>Mode</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Requested by</TableHead>
                    <TableHead>Requested at</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((op) => (
                    <TableRow key={op.id}>
                      <TableCell>{op.id}</TableCell>
                      <TableCell>{op.database_name}</TableCell>
                      <TableCell>{op.mode}</TableCell>
                      <TableCell>
                        <RestoreStatusBadge status={op.status} />
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {op.requested_by} ({op.requested_by_channel})
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(op.requested_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button size="sm" variant="outline" onClick={() => setLogTarget(op.id)}>
                            View log
                          </Button>
                          {isAdmin ? (
                            <WithTooltip content={op.status !== "PENDING" ? "Only PENDING restores can be cancelled" : null}>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={op.status !== "PENDING" || cancelMutation.isPending}
                                onClick={() => cancelMutation.mutate(op.id)}
                              >
                                Cancel
                              </Button>
                            </WithTooltip>
                          ) : null}
                        </div>
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

      <RestoreLogDialog id={logTarget} onOpenChange={(open) => !open && setLogTarget(null)} />
    </div>
  );
}

function RestoreLogDialog({ id, onOpenChange }: { id: number | null; onOpenChange: (open: boolean) => void }) {
  const { token } = useAuth();
  const logQuery = useQuery({
    queryKey: queryKeys.restoreOperations.log(id ?? -1),
    queryFn: () => getRestoreOperationLog(token, id as number),
    enabled: id != null,
  });

  return (
    <Dialog open={id != null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Restore operation #{id} log</DialogTitle>
        </DialogHeader>
        {logQuery.isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : logQuery.isError ? (
          <ErrorState error={logQuery.error} onRetry={() => logQuery.refetch()} />
        ) : logQuery.data?.log ? (
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 text-xs">
            {logQuery.data.log}
          </pre>
        ) : (
          <p className="text-sm text-muted-foreground">No log output recorded.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}

function RestoreCreateForm() {
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();

  const [sqlInstanceId, setSqlInstanceId] = useState("");
  const [backupJobId, setBackupJobId] = useState("");
  const [backupRecordId, setBackupRecordId] = useState("");
  const [databaseName, setDatabaseName] = useState("");
  const [confirmationName, setConfirmationName] = useState("");
  const [mode, setMode] = useState<RestoreMode>("MISSING");
  const [operatorRecordPreview, setOperatorRecordPreview] = useState<BackupRecordRead | null | "error">(null);

  const sqlInstancesQuery = useQuery({
    queryKey: queryKeys.sqlInstances.list({}),
    queryFn: () => listSqlInstances(token, { limit: 200 }),
  });

  const backupJobsQuery = useQuery({
    queryKey: queryKeys.backupJobs.list({}),
    queryFn: () => listBackupJobs(token, { limit: 200 }),
    enabled: isAdmin,
  });

  const backupRecordsQuery = useQuery({
    queryKey: queryKeys.backupRecords.list({ backup_job_id: backupJobId }),
    queryFn: () => listBackupRecords(token, { backup_job_id: Number(backupJobId), limit: 200 }),
    enabled: isAdmin && Boolean(backupJobId),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createRestoreOperation(token, {
        backup_record_id: Number(backupRecordId),
        sql_instance_id: Number(sqlInstanceId),
        database_name: databaseName,
        confirmation_database_name: confirmationName,
        mode: isAdmin ? mode : "MISSING",
      }),
    onSuccess: () => {
      toast.success("Restore operation requested");
      setBackupRecordId("");
      setDatabaseName("");
      setConfirmationName("");
      setOperatorRecordPreview(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.restoreOperations.all() });
    },
    onError: (error) => {
      // Defensive: even though the UI prevents an OPERATOR from submitting
      // mode != MISSING, still handle a 403 gracefully if the role/permission
      // changed server-side mid-session.
      toast.error(error instanceof ApiError ? error.detail : "Failed to create restore operation");
    },
  });

  async function handleOperatorRecordBlur(value: string) {
    setBackupRecordId(value);
    if (!value) {
      setOperatorRecordPreview(null);
      return;
    }
    try {
      const record = await getBackupRecord(token, Number(value));
      setOperatorRecordPreview(record);
    } catch {
      setOperatorRecordPreview("error");
    }
  }

  const namesMatch = databaseName.length > 0 && databaseName === confirmationName;
  const canSubmit = Boolean(sqlInstanceId) && Boolean(backupRecordId) && namesMatch && !createMutation.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle>New restore</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <label className="text-sm font-medium">SQL instance</label>
            <Select value={sqlInstanceId} onValueChange={setSqlInstanceId}>
              <SelectTrigger>
                <SelectValue placeholder="Select a SQL instance" />
              </SelectTrigger>
              <SelectContent>
                {sqlInstancesQuery.data?.items.map((si) => (
                  <SelectItem key={si.id} value={String(si.id)}>
                    {si.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isAdmin ? (
            <div className="space-y-1">
              <label className="text-sm font-medium">Backup job (to find records)</label>
              <Select
                value={backupJobId}
                onValueChange={(v) => {
                  setBackupJobId(v);
                  setBackupRecordId("");
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a backup job" />
                </SelectTrigger>
                <SelectContent>
                  {backupJobsQuery.data?.items.map((j) => (
                    <SelectItem key={j.id} value={String(j.id)}>
                      {j.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </div>

        {isAdmin ? (
          <div className="space-y-1">
            <label className="text-sm font-medium">Backup record</label>
            <Select value={backupRecordId} onValueChange={setBackupRecordId} disabled={!backupJobId}>
              <SelectTrigger>
                <SelectValue placeholder={backupJobId ? "Select a backup record" : "Select a backup job first"} />
              </SelectTrigger>
              <SelectContent>
                {backupRecordsQuery.data?.items.map((r) => (
                  <SelectItem key={r.id} value={String(r.id)}>
                    {r.file_name} ({r.remote_path})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : (
          <div className="space-y-1">
            <label className="text-sm font-medium">Backup record ID</label>
            <Input
              type="number"
              value={backupRecordId}
              onChange={(e) => setBackupRecordId(e.target.value)}
              onBlur={(e) => handleOperatorRecordBlur(e.target.value)}
            />
            {operatorRecordPreview === "error" ? (
              <p className="text-xs text-destructive">Backup record not found.</p>
            ) : operatorRecordPreview ? (
              <p className="text-xs text-muted-foreground">
                {operatorRecordPreview.file_name} — {operatorRecordPreview.remote_path}
              </p>
            ) : null}
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <label className="text-sm font-medium">Database name</label>
            <Input value={databaseName} onChange={(e) => setDatabaseName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <label className="text-sm font-medium">Confirm database name</label>
            <Input value={confirmationName} onChange={(e) => setConfirmationName(e.target.value)} />
            {confirmationName.length > 0 && !namesMatch ? (
              <p className="text-xs text-destructive">Must exactly match the database name above.</p>
            ) : null}
          </div>
        </div>

        {isAdmin ? (
          <div className="space-y-1">
            <label className="text-sm font-medium">Mode</label>
            <Select value={mode} onValueChange={(v) => setMode(v as RestoreMode)}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">ALL</SelectItem>
                <SelectItem value="EXISTING">EXISTING</SelectItem>
                <SelectItem value="MISSING">MISSING</SelectItem>
              </SelectContent>
            </Select>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            Operators can only request MISSING-mode restores (only missing objects are restored; existing data is
            never overwritten).
          </p>
        )}

        <Button disabled={!canSubmit} onClick={() => createMutation.mutate()}>
          {createMutation.isPending ? "Submitting…" : "Request restore"}
        </Button>
      </CardContent>
    </Card>
  );
}
