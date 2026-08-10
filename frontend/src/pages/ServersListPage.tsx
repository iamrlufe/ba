import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { Check, KeyRound, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { ServerStatusBadge } from "@/components/shared/StatusBadge";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import { createServer, listServers } from "@/api/endpoints/servers";
import { ApiError } from "@/api/client";
import { formatRelativeTime } from "@/lib/utils";
import type { ServerStatus } from "@/api/types";

const ALL = "__all__";

const serverCreateSchema = z
  .object({
    name: z.string().min(1, "Required"),
    host: z.string().min(1, "Required"),
    port: z.number().int().min(1).max(65535),
    protocol: z.enum(["FTP", "SFTP"]),
    notes: z.string().optional(),
    username: z.string().optional(),
    password: z.string().optional(),
    ssh_private_key: z.string().optional(),
  })
  .refine((data) => data.protocol !== "SFTP" || Boolean(data.password) || Boolean(data.ssh_private_key), {
    message: "SFTP servers require at least one of password or SSH private key",
    path: ["password"],
  });

type ServerCreateFormValues = z.infer<typeof serverCreateSchema>;

export function ServersListPage() {
  const { isAdmin, token } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const queryClient = useQueryClient();

  const status = searchParams.get("status") ?? undefined;
  const includeDeleted = searchParams.get("include_deleted") === "true";

  const query = usePaginatedQuery({
    queryKey: queryKeys.servers.list({ status, includeDeleted }),
    queryFn: ({ limit, offset }) =>
      listServers(token, { status, include_deleted: includeDeleted, limit, offset }),
  });

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL) next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const form = useForm<ServerCreateFormValues>({
    resolver: zodResolver(serverCreateSchema),
    defaultValues: { name: "", host: "", port: 21, protocol: "FTP", notes: "", username: "", password: "", ssh_private_key: "" },
  });
  const protocol = form.watch("protocol");

  const createMutation = useMutation({
    mutationFn: (values: ServerCreateFormValues) =>
      createServer(token, {
        name: values.name,
        host: values.host,
        port: values.port,
        protocol: values.protocol,
        notes: values.notes || null,
        username: values.username || null,
        password: values.password || null,
        ssh_private_key: values.ssh_private_key || null,
      }),
    onSuccess: () => {
      toast.success("Server created");
      setCreateOpen(false);
      form.reset();
      void queryClient.invalidateQueries({ queryKey: queryKeys.servers.all() });
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to create server");
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Servers</h1>
        {isAdmin ? (
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button>New server</Button>
            </DialogTrigger>
            <DialogContent className="max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Create server</DialogTitle>
                <DialogDescription>Register a new FTP/SFTP backup source server.</DialogDescription>
              </DialogHeader>
              <Form {...form}>
                <form
                  className="space-y-4"
                  onSubmit={form.handleSubmit((values) => createMutation.mutate(values))}
                >
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
                            <Input
                              type="number"
                              {...field}
                              onChange={(e) => field.onChange(e.target.value === "" ? undefined : Number(e.target.value))}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                  <FormField
                    control={form.control}
                    name="protocol"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Protocol</FormLabel>
                        <Select value={field.value} onValueChange={field.onChange}>
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            <SelectItem value="FTP">FTP</SelectItem>
                            <SelectItem value="SFTP">SFTP</SelectItem>
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="username"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Username (optional)</FormLabel>
                        <FormControl>
                          <Input {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {protocol === "SFTP" ? (
                    <>
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
                      <FormField
                        control={form.control}
                        name="ssh_private_key"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>SSH private key</FormLabel>
                            <FormControl>
                              <Input {...field} />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <p className="text-xs text-muted-foreground">
                        SFTP requires a password or an SSH private key (or both).
                      </p>
                    </>
                  ) : (
                    <FormField
                      control={form.control}
                      name="password"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Password (optional)</FormLabel>
                          <FormControl>
                            <Input type="password" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}
                  <DialogFooter>
                    <Button type="submit" disabled={createMutation.isPending}>
                      {createMutation.isPending ? "Creating…" : "Create"}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            </DialogContent>
          </Dialog>
        ) : null}
      </div>

      <div className="flex gap-3">
        <Select value={status ?? ALL} onValueChange={(v) => updateFilter("status", v)}>
          <SelectTrigger className="w-48">
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
      </div>

      <Card>
        <CardContent className="pt-6">
          {query.isLoading ? (
            <TableSkeleton />
          ) : query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState
              title="No servers yet"
              description={isAdmin ? "Create your first server to get started." : "No servers have been registered."}
              action={
                isAdmin ? (
                  <Button onClick={() => setCreateOpen(true)} size="sm">
                    New server
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Host</TableHead>
                    <TableHead>Protocol</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Credentials</TableHead>
                    <TableHead>Last seen</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((server) => (
                    <TableRow key={server.id} className="cursor-pointer">
                      <TableCell>
                        <Link to={`/servers/${server.id}`} className="font-medium hover:underline">
                          {server.name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {server.host}:{server.port}
                      </TableCell>
                      <TableCell>{server.protocol}</TableCell>
                      <TableCell>
                        <ServerStatusBadge status={server.status} />
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          {server.credentials_set ? (
                            <KeyRound className="h-4 w-4" aria-label="Credentials set" />
                          ) : (
                            <X className="h-4 w-4" aria-label="No credentials" />
                          )}
                          {server.ssh_key_set ? <Check className="h-4 w-4" aria-label="SSH key set" /> : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatRelativeTime(server.last_seen_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <PaginationBar
                total={query.data?.total ?? 0}
                limit={query.limit}
                offset={query.offset}
                onOffsetChange={query.setOffset}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
