import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { CardSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { DiskUsageBar } from "@/components/shared/DiskUsageBar";
import { ServerStatusBadge, JobRunStatusBadge } from "@/components/shared/StatusBadge";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { WithTooltip } from "@/components/shared/WithTooltip";
import { useAuth } from "@/auth/AuthContext";
import { queryKeys } from "@/api/queryKeys";
import { deleteServer, getServer, updateServer } from "@/api/endpoints/servers";
import { listDisks } from "@/api/endpoints/disks";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { listJobRuns } from "@/api/endpoints/jobRuns";
import { ApiError } from "@/api/client";
import { formatDateTime, formatRelativeTime } from "@/lib/utils";
import type { BackupJobRead, ServerRead } from "@/api/types";

export function ServerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const serverId = Number(id);
  const { isAdmin, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const serverQuery = useQuery({
    queryKey: queryKeys.servers.detail(serverId),
    queryFn: () => getServer(token, serverId),
    enabled: Number.isFinite(serverId),
  });

  const disksQuery = useQuery({
    queryKey: queryKeys.disks.list({ server_id: serverId }),
    queryFn: () => listDisks(token, { server_id: serverId, limit: 200 }),
    enabled: Number.isFinite(serverId),
  });

  const jobsQuery = useQuery({
    queryKey: queryKeys.backupJobs.list({ server_id: serverId }),
    queryFn: () => listBackupJobs(token, { server_id: serverId, limit: 200 }),
    enabled: Number.isFinite(serverId),
  });

  const hasEnabledJobs = (jobsQuery.data?.items ?? []).some((j) => j.is_enabled);

  const deleteMutation = useMutation({
    mutationFn: () => deleteServer(token, serverId),
    onSuccess: () => {
      toast.success("Server deleted");
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers.all() });
      navigate("/servers");
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to delete server");
    },
    onSettled: () => setDeleteOpen(false),
  });

  if (serverQuery.isLoading) return <CardSkeleton />;
  if (serverQuery.isError) return <ErrorState error={serverQuery.error} onRetry={() => serverQuery.refetch()} backTo="/servers" />;
  const server = serverQuery.data;
  if (!server) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{server.name}</h1>
          <p className="text-sm text-muted-foreground">
            {server.protocol} · {server.host}:{server.port}
          </p>
        </div>
        {isAdmin ? (
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              Edit
            </Button>
            <WithTooltip content={hasEnabledJobs ? "Cannot delete: server has enabled backup jobs" : null}>
              <Button variant="destructive" disabled={hasEnabledJobs} onClick={() => setDeleteOpen(true)}>
                Delete
              </Button>
            </WithTooltip>
          </div>
        ) : null}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Server info</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
          <Field label="Status">
            <ServerStatusBadge status={server.status} />
          </Field>
          <Field label="Credentials set">{server.credentials_set ? "Yes" : "No"}</Field>
          <Field label="SSH key set">{server.ssh_key_set ? "Yes" : "No"}</Field>
          <Field label="Last seen">{formatRelativeTime(server.last_seen_at)}</Field>
          <Field label="Created">{formatDateTime(server.created_at)}</Field>
          <Field label="Updated">{formatDateTime(server.updated_at)}</Field>
          {server.notes ? (
            <div className="col-span-full">
              <p className="text-xs font-medium text-muted-foreground">Notes</p>
              <p className="whitespace-pre-wrap">{server.notes}</p>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Disks</CardTitle>
        </CardHeader>
        <CardContent>
          {disksQuery.isLoading ? (
            <CardSkeleton />
          ) : disksQuery.isError ? (
            <ErrorState error={disksQuery.error} onRetry={() => disksQuery.refetch()} />
          ) : disksQuery.data && disksQuery.data.items.length === 0 ? (
            <EmptyState title="No disks registered" />
          ) : (
            <div className="space-y-3">
              {disksQuery.data?.items.map((disk) => (
                <div key={disk.id} className="space-y-1">
                  <p className="text-sm font-medium">
                    {disk.label} <span className="text-muted-foreground">({disk.mount_path})</span>
                  </p>
                  <DiskUsageBar disk={disk} />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Backup jobs</CardTitle>
        </CardHeader>
        <CardContent>
          {jobsQuery.isLoading ? (
            <CardSkeleton />
          ) : jobsQuery.isError ? (
            <ErrorState error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />
          ) : jobsQuery.data && jobsQuery.data.items.length === 0 ? (
            <EmptyState title="No backup jobs for this server" />
          ) : (
            <div className="space-y-6">
              {jobsQuery.data?.items.map((job) => <JobWithRuns key={job.id} job={job} />)}
            </div>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete server?"
        description={`This will soft-delete "${server.name}". This action cannot be undone from the UI.`}
        destructive
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />

      <ServerEditDialog open={editOpen} onOpenChange={setEditOpen} server={server} />
    </div>
  );
}

interface ServerEditFormValues {
  name: string;
  host: string;
  port: number;
  notes: string;
  changeCredentials: boolean;
  username: string;
  password: string;
  ssh_private_key: string;
}

/**
 * Edit dialog for a server. Credential fields are entry-only and always
 * start blank -- the "Change credentials" toggle reveals them; leaving it
 * off omits those keys from the PATCH entirely (server-side: absent key =
 * unchanged, explicit "" = clear -- see ServerUpdate docstring).
 */
function ServerEditDialog({
  open,
  onOpenChange,
  server,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: ServerRead;
}) {
  const { token } = useAuth();
  const queryClient = useQueryClient();

  const form = useForm<ServerEditFormValues>({
    values: {
      name: server.name,
      host: server.host,
      port: server.port,
      notes: server.notes ?? "",
      changeCredentials: false,
      username: "",
      password: "",
      ssh_private_key: "",
    },
  });
  const changeCredentials = form.watch("changeCredentials");

  const mutation = useMutation({
    mutationFn: (values: ServerEditFormValues) => {
      const payload: Record<string, unknown> = {
        name: values.name,
        host: values.host,
        port: values.port,
        notes: values.notes || null,
      };
      if (values.changeCredentials) {
        // Only include fields the admin actually typed into -- the
        // placeholder ("unchanged") promises that leaving a field blank
        // preserves it, but the backend treats an explicit "" as "clear
        // this secret." Sending every field unconditionally here would
        // silently wipe any credential the admin didn't mean to touch
        // (e.g. rotating only the password would blank out the username).
        if (values.username.trim()) payload.username = values.username;
        if (values.password.trim()) payload.password = values.password;
        if (values.ssh_private_key.trim()) payload.ssh_private_key = values.ssh_private_key;
      }
      return updateServer(token, server.id, payload);
    },
    onSuccess: () => {
      toast.success("Server updated");
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers.all() });
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to update server");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit server</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form className="space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="host"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Host</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="port"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Port</FormLabel>
                    <FormControl>
                      <Input type="number" {...field} onChange={(e) => field.onChange(Number(e.target.value))} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="notes"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Notes</FormLabel>
                  <FormControl>
                    <Textarea {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex items-center gap-2">
              <input
                id="change-credentials"
                type="checkbox"
                className="h-4 w-4"
                checked={changeCredentials}
                onChange={(e) => form.setValue("changeCredentials", e.target.checked)}
              />
              <label htmlFor="change-credentials" className="text-sm">
                Change credentials
              </label>
            </div>

            {changeCredentials ? (
              <div className="space-y-4 rounded-md border p-3">
                <FormField
                  control={form.control}
                  name="username"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Username</FormLabel>
                      <FormControl>
                        <Input placeholder="•••••• (unchanged)" {...field} />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Password</FormLabel>
                      <FormControl>
                        <Input type="password" placeholder="•••••• (unchanged)" {...field} />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="ssh_private_key"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>SSH private key</FormLabel>
                      <FormControl>
                        <Textarea placeholder="•••••• (unchanged)" {...field} />
                      </FormControl>
                    </FormItem>
                  )}
                />
              </div>
            ) : null}

            <DialogFooter>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Saving…" : "Save changes"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
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

/**
 * Per-job-run history table. job_runs has no server_id filter, so this
 * queries by backup_job_id per row instead of trying to flatten a
 * server-wide run list client-side (would require N+1 joins).
 */
function JobWithRuns({ job }: { job: BackupJobRead }) {
  const { token } = useAuth();
  const runsQuery = useQuery({
    queryKey: queryKeys.jobRuns.list({ backup_job_id: job.id, limit: 5 }),
    queryFn: () => listJobRuns(token, { backup_job_id: job.id, limit: 5 }),
  });

  return (
    <div className="rounded-md border p-4">
      <div className="mb-2 flex items-center justify-between">
        <Link to={`/jobs/${job.id}`} className="font-medium hover:underline">
          {job.name}
        </Link>
        <Badge variant={job.is_enabled ? "success" : "secondary"}>{job.is_enabled ? "Enabled" : "Disabled"}</Badge>
      </div>
      {runsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading recent runs…</p>
      ) : runsQuery.isError ? (
        <ErrorState error={runsQuery.error} onRetry={() => runsQuery.refetch()} />
      ) : runsQuery.data && runsQuery.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No runs yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Finished</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runsQuery.data?.items.map((run) => (
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
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
