import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { TableSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { PaginationBar } from "@/components/shared/PaginationBar";
import { useAuth } from "@/auth/AuthContext";
import { usePaginatedQuery } from "@/hooks/usePaginatedQuery";
import { queryKeys } from "@/api/queryKeys";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { listServers } from "@/api/endpoints/servers";
import { formatDateTime } from "@/lib/utils";

const ALL = "__all__";

export function JobsListPage() {
  const { isAdmin, token } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const serverIdParam = searchParams.get("server_id");
  const serverId = serverIdParam ? Number(serverIdParam) : undefined;
  const isEnabledParam = searchParams.get("is_enabled");
  const isEnabled = isEnabledParam === "true" ? true : isEnabledParam === "false" ? false : undefined;

  const serversQuery = useQuery({
    queryKey: queryKeys.servers.list({}),
    queryFn: () => listServers(token, { limit: 200 }),
  });

  const query = usePaginatedQuery({
    queryKey: queryKeys.backupJobs.list({ serverId, isEnabled }),
    queryFn: ({ limit, offset }) => listBackupJobs(token, { server_id: serverId, is_enabled: isEnabled, limit, offset }),
  });

  function updateFilter(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null || value === ALL) next.delete(key);
    else next.set(key, value);
    next.delete("offset");
    setSearchParams(next, { replace: true });
  }

  const serverNameById = new Map((serversQuery.data?.items ?? []).map((s) => [s.id, s.name]));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Backup jobs</h1>
        {isAdmin ? <Button onClick={() => navigate("/jobs/new")}>New job</Button> : null}
      </div>

      <Alert>
        <Info className="h-4 w-4" />
        <AlertDescription>
          You can create multiple backup jobs for the same server to handle different copy
          types (FULL/DIFFERENTIAL/TRANSACTION_LOG) with different source paths and schedules.
        </AlertDescription>
      </Alert>

      <div className="flex gap-3">
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
        <Select value={isEnabledParam ?? ALL} onValueChange={(v) => updateFilter("is_enabled", v)}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All</SelectItem>
            <SelectItem value="true">Enabled</SelectItem>
            <SelectItem value="false">Disabled</SelectItem>
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
              title="No backup jobs found"
              description={isAdmin ? "Create a job to start protecting a server." : "No jobs match these filters."}
              action={isAdmin ? <Button size="sm" onClick={() => navigate("/jobs/new")}>New job</Button> : undefined}
            />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Server</TableHead>
                    <TableHead>Schedule</TableHead>
                    <TableHead>Last run</TableHead>
                    <TableHead>Next run</TableHead>
                    <TableHead>Enabled</TableHead>
                    <TableHead>Verified</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {query.data?.items.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell>
                        <Link to={`/jobs/${job.id}`} className="font-medium hover:underline">
                          {job.name}
                        </Link>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {serverNameById.get(job.server_id) ?? `#${job.server_id}`}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {job.trigger_mode === "WATCH" ? `Watch: ${job.watch_directory}` : job.schedule_cron}
                      </TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(job.last_run_at)}</TableCell>
                      <TableCell className="text-muted-foreground">{formatDateTime(job.next_run_at)}</TableCell>
                      <TableCell>
                        <Badge variant={job.is_enabled ? "success" : "secondary"}>
                          {job.is_enabled ? "Enabled" : "Disabled"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {job.sql_instance_id != null ? (
                          <CheckCircle2 className="h-4 w-4 text-success" aria-label="Verification enabled" />
                        ) : (
                          <XCircle className="h-4 w-4 text-muted-foreground" aria-label="Verification disabled" />
                        )}
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
    </div>
  );
}
