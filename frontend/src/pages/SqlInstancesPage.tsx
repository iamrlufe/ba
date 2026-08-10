import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { KeyRound, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { ConfirmDialog } from "@/components/shared/ConfirmDialog";
import { WithTooltip } from "@/components/shared/WithTooltip";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import {
  createSqlInstance,
  deleteSqlInstance,
  listSqlInstances,
  updateSqlInstance,
} from "@/api/endpoints/sqlInstances";
import { listServers } from "@/api/endpoints/servers";
import { ApiError } from "@/api/client";
import { formatDateTime } from "@/lib/utils";
import type { ServerStatus, SqlInstanceRead } from "@/api/types";

const ALL = "__all__";
const NONE = "__none__";

interface SqlInstanceFormValues {
  name: string;
  host: string;
  server_id: string;
  authMode: "windows" | "sql";
  port: string;
  instance_name: string;
  notes: string;
  changeCredentials: boolean;
  username: string;
  password: string;
}

const emptyValues: SqlInstanceFormValues = {
  name: "",
  host: "",
  server_id: NONE,
  authMode: "sql",
  port: "",
  instance_name: "",
  notes: "",
  changeCredentials: true,
  username: "",
  password: "",
};

export function SqlInstancesPage() {
  const { isAdmin, token } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SqlInstanceRead | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SqlInstanceRead | null>(null);

  const status = (searchParams.get("status") as ServerStatus | null) ?? undefined;
  const serverIdParam = searchParams.get("server_id");
  const serverId = serverIdParam ? Number(serverIdParam) : undefined;

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL) next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const serversQuery = useQuery({
    queryKey: queryKeys.servers.list({}),
    queryFn: () => listServers(token, { limit: 200 }),
  });

  const query = usePaginatedQuery({
    queryKey: queryKeys.sqlInstances.list({ status, serverId }),
    queryFn: ({ limit, offset }) => listSqlInstances(token, { status, server_id: serverId, limit, offset }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteSqlInstance(token, id),
    onSuccess: () => {
      toast.success("SQL instance deleted");
      void queryClient.invalidateQueries({ queryKey: queryKeys.sqlInstances.all() });
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.detail : "Failed to delete SQL instance"),
    onSettled: () => setDeleteTarget(null),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">SQL instances</h1>
        {isAdmin ? (
          <Button onClick={() => setCreateOpen(true)}>New SQL instance</Button>
        ) : null}
      </div>

      <div className="flex gap-3">
        <Select value={status ?? ALL} onValueChange={(v) => updateFilter("status", v)}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {(["ACTIVE", "DISABLED", "UNREACHABLE", "OFFLINE"] as ServerStatus[]).map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={serverIdParam ?? ALL} onValueChange={(v) => updateFilter("server_id", v)}>
          <SelectTrigger className="w-56">
            <SelectValue placeholder="All servers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All servers</SelectItem>
            {serversQuery.data?.items.map((s) => (
              <SelectItem key={s.id} value={String(s.id)}>
                {s.name}
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
            <EmptyState
              title="No SQL instances yet"
              action={isAdmin ? <Button size="sm" onClick={() => setCreateOpen(true)}>New SQL instance</Button> : undefined}
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Host</TableHead>
                    <TableHead>Credentials</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last verified</TableHead>
                    {isAdmin ? <TableHead className="text-right">Actions</TableHead> : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((instance) => {
                    const hasEnabled = false; // determined server-side on delete; UI just surfaces the 409 if it happens
                    return (
                      <TableRow key={instance.id}>
                        <TableCell className="font-medium">{instance.name}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {instance.host}
                          {instance.port ? `:${instance.port}` : ""}
                          {instance.instance_name ? `\\${instance.instance_name}` : ""}
                        </TableCell>
                        <TableCell>
                          {instance.use_windows_auth ? (
                            "Windows auth"
                          ) : instance.credentials_set ? (
                            <KeyRound className="h-4 w-4 text-muted-foreground" aria-label="Credentials set" />
                          ) : (
                            <X className="h-4 w-4 text-muted-foreground" aria-label="No credentials" />
                          )}
                        </TableCell>
                        <TableCell>{instance.status}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {formatDateTime(instance.last_verified_connection_at)}
                        </TableCell>
                        {isAdmin ? (
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-2">
                              <Button size="sm" variant="outline" onClick={() => setEditTarget(instance)}>
                                Edit
                              </Button>
                              <WithTooltip content={hasEnabled ? "Cannot delete: referenced by active resources" : null}>
                                <Button size="sm" variant="destructive" onClick={() => setDeleteTarget(instance)}>
                                  Delete
                                </Button>
                              </WithTooltip>
                            </div>
                          </TableCell>
                        ) : null}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <PaginationBar total={query.data?.total ?? 0} limit={query.limit} offset={query.offset} onOffsetChange={query.setOffset} />
            </>
          )}
        </CardContent>
      </Card>

      <SqlInstanceFormDialog open={createOpen} onOpenChange={setCreateOpen} mode="create" />
      <SqlInstanceFormDialog
        open={editTarget != null}
        onOpenChange={(open) => !open && setEditTarget(null)}
        mode="edit"
        instance={editTarget}
      />

      <ConfirmDialog
        open={deleteTarget != null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title="Delete SQL instance?"
        description={`This will soft-delete "${deleteTarget?.name}".`}
        destructive
        isConfirming={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
      />
    </div>
  );
}

function SqlInstanceFormDialog({
  open,
  onOpenChange,
  mode,
  instance,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: "create" | "edit";
  instance?: SqlInstanceRead | null;
}) {
  const { token } = useAuth();
  const queryClient = useQueryClient();

  const serversQuery = useQuery({
    queryKey: queryKeys.servers.list({}),
    queryFn: () => listServers(token, { limit: 200 }),
  });

  const form = useForm<SqlInstanceFormValues>({
    values:
      mode === "edit" && instance
        ? {
            name: instance.name,
            host: instance.host,
            server_id: instance.server_id != null ? String(instance.server_id) : NONE,
            authMode: instance.use_windows_auth ? "windows" : "sql",
            port: instance.port != null ? String(instance.port) : "",
            instance_name: instance.instance_name ?? "",
            notes: instance.notes ?? "",
            changeCredentials: false,
            username: "",
            password: "",
          }
        : emptyValues,
  });

  const authMode = form.watch("authMode");
  const changeCredentials = form.watch("changeCredentials");

  const mutation = useMutation({
    mutationFn: async (values: SqlInstanceFormValues) => {
      const useWindowsAuth = values.authMode === "windows";
      const common = {
        name: values.name,
        host: values.host,
        server_id: values.server_id === NONE ? null : Number(values.server_id),
        port: values.port ? Number(values.port) : null,
        instance_name: values.instance_name || null,
        use_windows_auth: useWindowsAuth,
        notes: values.notes || null,
      };
      if (mode === "create") {
        return createSqlInstance(token, {
          ...common,
          username: !useWindowsAuth ? values.username : null,
          password: !useWindowsAuth ? values.password : null,
        });
      }
      const patch: Record<string, unknown> = { ...common };
      if (values.changeCredentials && !useWindowsAuth) {
        // Only include a field the admin actually typed into -- an
        // unconditional send would silently wipe whichever of
        // username/password was left blank trusting the "unchanged"
        // placeholder (the backend treats explicit "" as "clear").
        if (values.username.trim()) patch.username = values.username;
        if (values.password.trim()) patch.password = values.password;
      }
      return updateSqlInstance(token, instance!.id, patch);
    },
    onSuccess: () => {
      toast.success(mode === "create" ? "SQL instance created" : "SQL instance updated");
      onOpenChange(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.sqlInstances.all() });
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to save SQL instance");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "Create SQL instance" : "Edit SQL instance"}</DialogTitle>
          <DialogDescription>Port and named instance are mutually exclusive.</DialogDescription>
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
            <div className="grid grid-cols-2 gap-4">
              <FormField
                control={form.control}
                name="port"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Port</FormLabel>
                    <FormControl>
                      <Input type="number" placeholder="optional" disabled={Boolean(form.watch("instance_name"))} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="instance_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Named instance</FormLabel>
                    <FormControl>
                      <Input placeholder="optional" disabled={Boolean(form.watch("port"))} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <FormField
              control={form.control}
              name="server_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Server (optional)</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value={NONE}>None</SelectItem>
                      {serversQuery.data?.items.map((s) => (
                        <SelectItem key={s.id} value={String(s.id)}>
                          {s.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="authMode"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between rounded-md border p-3">
                  <FormLabel className="mb-0">Use Windows auth</FormLabel>
                  <FormControl>
                    <Switch
                      checked={field.value === "windows"}
                      onCheckedChange={(checked) => field.onChange(checked ? "windows" : "sql")}
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            {authMode === "sql" ? (
              mode === "create" ? (
                <>
                  <FormField
                    control={form.control}
                    name="username"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Username</FormLabel>
                        <FormControl>
                          <Input {...field} />
                        </FormControl>
                        <FormMessage />
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
                          <Input type="password" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <input
                      id="change-sql-credentials"
                      type="checkbox"
                      className="h-4 w-4"
                      checked={changeCredentials}
                      onChange={(e) => form.setValue("changeCredentials", e.target.checked)}
                    />
                    <label htmlFor="change-sql-credentials" className="text-sm">
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
                    </div>
                  ) : null}
                </>
              )
            ) : null}

            <DialogFooter>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "Saving…" : mode === "create" ? "Create" : "Save changes"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
