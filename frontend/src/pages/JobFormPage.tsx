import { useEffect, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CronExpressionParser } from "cron-parser";
import { HelpCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage, FormDescription } from "@/components/ui/form";
import { CardSkeleton } from "@/components/shared/LoadingState";
import { ErrorState } from "@/components/shared/ErrorState";
import { WithTooltip } from "@/components/shared/WithTooltip";
import { useAuth } from "@/auth/AuthContext";
import { queryKeys } from "@/api/queryKeys";
import { listServers } from "@/api/endpoints/servers";
import { listDisks } from "@/api/endpoints/disks";
import { listSqlInstances } from "@/api/endpoints/sqlInstances";
import { createBackupJob, getBackupJob, updateBackupJob } from "@/api/endpoints/backupJobs";
import { ApiError } from "@/api/client";
import type { BackupJobUpdate } from "@/api/types";

const NONE = "__none__";

const DEFAULT_JOB_TIMEZONE: string = import.meta.env.VITE_DEFAULT_JOB_TIMEZONE ?? "Asia/Tashkent";

const jobFormSchema = z
  .object({
    name: z.string().min(1, "Required").max(255),
    database_name: z.string().max(255).optional(),
    source_path: z.string().max(500).optional(),
    backup_type: z.enum(["FULL", "DIFFERENTIAL", "TRANSACTION_LOG", "CUSTOM"]),
    trigger_mode: z.enum(["SCHEDULE", "WATCH"]),
    watch_directory: z.string().max(500).optional(),
    schedule_cron: z.string().max(120).optional(),
    timezone: z.string().min(1).max(64),
    retention_days: z.number().int().gt(0),
    retention_min_copies: z.number().int().min(0),
    verification_method: z.string().max(50).optional(),
    expected_max_duration_minutes: z.string().optional(),
    missed_run_grace_minutes: z.number().int().gt(0),
    copy_window_start_hour: z.number().int().min(0).max(23).optional(),
    copy_window_end_hour: z.number().int().min(0).max(23).optional(),
    copy_window_weekend_unrestricted: z.boolean(),
    local_backup_path_pattern: z.string().max(500).optional(),
    server_id: z.string().min(1, "Required"),
    disk_id: z.string().min(1, "Required"),
    sql_instance_id: z.string(),
    is_enabled: z.boolean(),
  })
  .superRefine((data, ctx) => {
    if (data.sql_instance_id !== NONE && data.sql_instance_id !== "") {
      if (!data.verification_method) {
        ctx.addIssue({
          code: "custom",
          message: "Required when a SQL instance is selected",
          path: ["verification_method"],
        });
      }
      if (!data.database_name) {
        ctx.addIssue({
          code: "custom",
          message: "Required when a SQL instance is selected (needed for msdb verification queries)",
          path: ["database_name"],
        });
      }
    }

    // --- trigger_mode cross-field rules ---
    if (data.trigger_mode === "SCHEDULE") {
      if (!data.source_path) {
        ctx.addIssue({ code: "custom", message: "Required for scheduled jobs", path: ["source_path"] });
      }
      if (!data.schedule_cron) {
        ctx.addIssue({ code: "custom", message: "Required for scheduled jobs", path: ["schedule_cron"] });
      }
    } else if (data.trigger_mode === "WATCH") {
      if (!data.watch_directory) {
        ctx.addIssue({ code: "custom", message: "Required for watch-mode jobs", path: ["watch_directory"] });
      }
      if (data.backup_type === "TRANSACTION_LOG" || data.backup_type === "CUSTOM") {
        ctx.addIssue({
          code: "custom",
          message:
            'Watch-mode jobs don\'t support Transaction Log or Custom backup types (sequential/cumulative backups can\'t safely use "latest file wins" transfer semantics)',
          path: ["backup_type"],
        });
      }
    }

    // --- copy-window both-or-neither + start != end ---
    const startSet = data.copy_window_start_hour !== undefined && data.copy_window_start_hour !== null;
    const endSet = data.copy_window_end_hour !== undefined && data.copy_window_end_hour !== null;
    if (startSet !== endSet) {
      ctx.addIssue({
        code: "custom",
        message: "Set both start and end hour, or leave both empty",
        path: [startSet ? "copy_window_end_hour" : "copy_window_start_hour"],
      });
    } else if (startSet && endSet && data.copy_window_start_hour === data.copy_window_end_hour) {
      ctx.addIssue({
        code: "custom",
        message: "Start and end hour must differ (an equal pair is not a valid window)",
        path: ["copy_window_end_hour"],
      });
    }
  });

type JobFormValues = z.infer<typeof jobFormSchema>;

const defaultValues: JobFormValues = {
  name: "",
  database_name: "",
  source_path: "",
  backup_type: "FULL",
  trigger_mode: "SCHEDULE",
  watch_directory: "",
  schedule_cron: "",
  timezone: DEFAULT_JOB_TIMEZONE,
  retention_days: 30,
  retention_min_copies: 1,
  verification_method: "",
  expected_max_duration_minutes: "",
  missed_run_grace_minutes: 60,
  copy_window_start_hour: undefined,
  copy_window_end_hour: undefined,
  copy_window_weekend_unrestricted: false,
  local_backup_path_pattern: "",
  server_id: "",
  disk_id: "",
  sql_instance_id: NONE,
  is_enabled: true,
};

export function JobFormPage({ mode }: { mode: "create" | "edit" }) {
  const { id } = useParams<{ id: string }>();
  const jobId = id ? Number(id) : undefined;
  const { token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const jobQuery = useQuery({
    queryKey: queryKeys.backupJobs.detail(jobId as number),
    queryFn: () => getBackupJob(token, jobId as number),
    enabled: mode === "edit" && jobId !== undefined,
  });

  const serversQuery = useQuery({
    queryKey: queryKeys.servers.list({}),
    queryFn: () => listServers(token, { limit: 200 }),
  });

  const form = useForm<JobFormValues>({
    resolver: zodResolver(jobFormSchema),
    defaultValues,
  });

  const serverId = form.watch("server_id");
  const triggerMode = form.watch("trigger_mode");
  const watchedScheduleCron = form.watch("schedule_cron");
  const watchedTimezone = form.watch("timezone");

  const nextRunPreview = useMemo(() => {
    if (!watchedScheduleCron) return "Enter a schedule to preview the next run";
    try {
      const next = CronExpressionParser.parse(watchedScheduleCron, { tz: watchedTimezone }).next().toDate();
      const local = next.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: watchedTimezone,
      });
      const utc = next.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      });
      return `Next run: ${local} (${watchedTimezone}) = ${utc} UTC`;
    } catch {
      return "Invalid schedule or timezone";
    }
  }, [watchedScheduleCron, watchedTimezone]);

  const disksQuery = useQuery({
    queryKey: queryKeys.disks.list({ server_id: serverId }),
    queryFn: () => listDisks(token, { server_id: Number(serverId), limit: 200 }),
    enabled: Boolean(serverId),
  });

  const sqlInstancesQuery = useQuery({
    queryKey: queryKeys.sqlInstances.list({}),
    queryFn: () => listSqlInstances(token, { limit: 200 }),
  });

  useEffect(() => {
    if (mode === "edit" && jobQuery.data) {
      const job = jobQuery.data;
      form.reset({
        name: job.name,
        database_name: job.database_name ?? "",
        source_path: job.source_path ?? "",
        backup_type: job.backup_type,
        trigger_mode: job.trigger_mode,
        watch_directory: job.watch_directory ?? "",
        schedule_cron: job.schedule_cron ?? "",
        timezone: job.timezone,
        retention_days: job.retention_days,
        retention_min_copies: job.retention_min_copies,
        verification_method: job.verification_method ?? "",
        expected_max_duration_minutes:
          job.expected_max_duration_minutes != null ? String(job.expected_max_duration_minutes) : "",
        missed_run_grace_minutes: job.missed_run_grace_minutes,
        copy_window_start_hour: job.copy_window_start_hour ?? undefined,
        copy_window_end_hour: job.copy_window_end_hour ?? undefined,
        copy_window_weekend_unrestricted: job.copy_window_weekend_unrestricted,
        local_backup_path_pattern: job.local_backup_path_pattern ?? "",
        server_id: String(job.server_id),
        disk_id: String(job.disk_id),
        sql_instance_id: job.sql_instance_id != null ? String(job.sql_instance_id) : NONE,
        is_enabled: job.is_enabled,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, jobQuery.data]);

  const createMutation = useMutation({
    mutationFn: (values: JobFormValues) =>
      createBackupJob(token, {
        name: values.name,
        database_name: values.database_name || null,
        source_path: values.trigger_mode === "SCHEDULE" ? values.source_path : null,
        backup_type: values.backup_type,
        trigger_mode: values.trigger_mode,
        watch_directory: values.trigger_mode === "WATCH" ? values.watch_directory || null : null,
        schedule_cron: values.trigger_mode === "SCHEDULE" ? values.schedule_cron : null,
        timezone: values.timezone,
        retention_days: values.retention_days,
        retention_min_copies: values.retention_min_copies,
        verification_method: values.verification_method || null,
        expected_max_duration_minutes: values.expected_max_duration_minutes
          ? Number(values.expected_max_duration_minutes)
          : null,
        missed_run_grace_minutes: values.missed_run_grace_minutes,
        copy_window_start_hour: values.copy_window_start_hour ?? null,
        copy_window_end_hour: values.copy_window_end_hour ?? null,
        copy_window_weekend_unrestricted: values.copy_window_weekend_unrestricted,
        local_backup_path_pattern: values.local_backup_path_pattern || null,
        server_id: Number(values.server_id),
        disk_id: Number(values.disk_id),
        sql_instance_id: values.sql_instance_id === NONE ? null : Number(values.sql_instance_id),
        is_enabled: values.is_enabled,
      }),
    onSuccess: (job) => {
      toast.success("Backup job created");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.all() });
      navigate(`/jobs/${job.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to create backup job");
    },
  });

  const updateMutation = useMutation({
    mutationFn: (values: JobFormValues) => {
      if (jobId === undefined) throw new Error("missing job id");
      const dirty = form.formState.dirtyFields;
      const patch: BackupJobUpdate = {};

      if (dirty.name) patch.name = values.name;
      if (dirty.database_name) patch.database_name = values.database_name || null;
      if (dirty.backup_type) patch.backup_type = values.backup_type;
      if (dirty.timezone) patch.timezone = values.timezone;
      if (dirty.retention_days) patch.retention_days = values.retention_days;
      if (dirty.retention_min_copies) patch.retention_min_copies = values.retention_min_copies;
      if (dirty.verification_method) patch.verification_method = values.verification_method || null;
      if (dirty.expected_max_duration_minutes) {
        patch.expected_max_duration_minutes = values.expected_max_duration_minutes
          ? Number(values.expected_max_duration_minutes)
          : null;
      }
      if (dirty.missed_run_grace_minutes) patch.missed_run_grace_minutes = values.missed_run_grace_minutes;
      if (dirty.local_backup_path_pattern) patch.local_backup_path_pattern = values.local_backup_path_pattern || null;
      if (dirty.is_enabled) patch.is_enabled = values.is_enabled;

      // trigger_mode switch requires the full coherent field set for the NEW
      // mode, plus explicitly nulling the OLD mode's now-invalid field(s), in
      // this SAME request -- the backend rejects a bare {trigger_mode: "..."}
      // patch that leaves stale fields from the old mode in place (409).
      if (dirty.trigger_mode) {
        patch.trigger_mode = values.trigger_mode;
        if (values.trigger_mode === "SCHEDULE") {
          patch.source_path = values.source_path || null;
          patch.schedule_cron = values.schedule_cron || null;
          patch.watch_directory = null;
        } else {
          patch.watch_directory = values.watch_directory || null;
          patch.source_path = null;
          patch.schedule_cron = null;
        }
      } else {
        if (dirty.source_path) patch.source_path = values.source_path || null;
        if (dirty.schedule_cron) patch.schedule_cron = values.schedule_cron || null;
        if (dirty.watch_directory) patch.watch_directory = values.watch_directory || null;
      }

      // Copy-window hours: both-or-neither is a property of the pair, so if
      // either is dirty, send both together.
      if (dirty.copy_window_start_hour || dirty.copy_window_end_hour) {
        patch.copy_window_start_hour = values.copy_window_start_hour ?? null;
        patch.copy_window_end_hour = values.copy_window_end_hour ?? null;
      }
      if (dirty.copy_window_weekend_unrestricted) {
        patch.copy_window_weekend_unrestricted = values.copy_window_weekend_unrestricted;
      }
      // sql_instance_id: absent key = unchanged; explicit null = clear verification.
      if (dirty.sql_instance_id) {
        patch.sql_instance_id = values.sql_instance_id === NONE ? null : Number(values.sql_instance_id);
      }

      return updateBackupJob(token, jobId, patch);
    },
    onSuccess: (job) => {
      toast.success("Backup job updated");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.detail(job.id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.all() });
      navigate(`/jobs/${job.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Failed to update backup job");
    },
  });

  const mutation = mode === "create" ? createMutation : updateMutation;

  if (mode === "edit" && jobQuery.isLoading) return <CardSkeleton />;
  if (mode === "edit" && jobQuery.isError) {
    return <ErrorState error={jobQuery.error} onRetry={() => jobQuery.refetch()} backTo="/jobs" />;
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">{mode === "create" ? "New backup job" : "Edit backup job"}</h1>
      <Card>
        <CardHeader>
          <CardTitle>Job details</CardTitle>
        </CardHeader>
        <CardContent>
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
                name="server_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Server</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(v) => {
                        // Radix Select's hidden native "bubble" mirror can fire
                        // onValueChange with "" when a controlled value is set
                        // programmatically (e.g. form.reset in edit mode) before
                        // the dropdown has ever been opened -- see JobFormPage's
                        // other guarded handlers for the same reason.
                        if (!v) return;
                        field.onChange(v);
                        form.setValue("disk_id", "", { shouldDirty: true });
                      }}
                      disabled={mode === "edit"}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a server" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {serversQuery.data?.items.map((s) => (
                          <SelectItem key={s.id} value={String(s.id)}>
                            {s.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {mode === "edit" ? <FormDescription>Server cannot be changed after creation.</FormDescription> : null}
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="disk_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Disk</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(v) => {
                        if (!v) return;
                        field.onChange(v);
                      }}
                      disabled={!serverId || mode === "edit"}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder={serverId ? "Select a disk" : "Select a server first"} />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {disksQuery.data?.items.map((d) => (
                          <SelectItem key={d.id} value={String(d.id)}>
                            {d.label} ({d.mount_path})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {mode === "edit" ? <FormDescription>Disk cannot be changed after creation.</FormDescription> : null}
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="trigger_mode"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Trigger mode</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(v) => {
                        if (!v) return;
                        field.onChange(v);
                        if (v === "WATCH") {
                          form.setValue("source_path", "", { shouldDirty: true });
                          form.setValue("schedule_cron", "", { shouldDirty: true });
                        } else {
                          form.setValue("watch_directory", "", { shouldDirty: true });
                        }
                      }}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="SCHEDULE">Schedule (cron)</SelectItem>
                        <SelectItem value="WATCH">Watch directory</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      Watch mode is not available for Transaction Log or Custom backup types.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {triggerMode === "SCHEDULE" ? (
                <FormField
                  control={form.control}
                  name="source_path"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Source path (remote)</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              ) : (
                <FormField
                  control={form.control}
                  name="watch_directory"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Watch directory (remote)</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormDescription>
                        Directory the agent watches for new/changed files, not a single file path.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="backup_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Backup type</FormLabel>
                      <Select value={field.value} onValueChange={(v) => v && field.onChange(v)}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {(["FULL", "DIFFERENTIAL", "TRANSACTION_LOG", "CUSTOM"] as const).map((t) => (
                            <SelectItem key={t} value={t}>
                              {t}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                {triggerMode === "SCHEDULE" ? (
                  <FormField
                    control={form.control}
                    name="schedule_cron"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="flex items-center gap-1">
                          Schedule (cron)
                          <WithTooltip content={nextRunPreview}>
                            <HelpCircle className="h-3.5 w-3.5 text-muted-foreground" />
                          </WithTooltip>
                        </FormLabel>
                        <FormControl>
                          <Input placeholder="0 2 * * *" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                ) : null}
              </div>

              <div className="grid grid-cols-3 gap-4">
                <FormField
                  control={form.control}
                  name="timezone"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Timezone</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="retention_days"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Retention (days)</FormLabel>
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
                <FormField
                  control={form.control}
                  name="retention_min_copies"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Min copies to keep</FormLabel>
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

              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="expected_max_duration_minutes"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Expected max duration (minutes)</FormLabel>
                      <FormControl>
                        <Input type="number" placeholder="optional" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="missed_run_grace_minutes"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Missed-run grace (minutes)</FormLabel>
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

              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="copy_window_start_hour"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Copy window start (hour, 0-23)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={0}
                          max={23}
                          placeholder="optional"
                          value={field.value ?? ""}
                          onChange={(e) => field.onChange(e.target.value === "" ? undefined : Number(e.target.value))}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="copy_window_end_hour"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Copy window end (hour, 0-23)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={0}
                          max={23}
                          placeholder="optional"
                          value={field.value ?? ""}
                          onChange={(e) => field.onChange(e.target.value === "" ? undefined : Number(e.target.value))}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
              <p className="text-[0.8rem] text-muted-foreground">
                Restricts when the agent may transfer a file, in the job&apos;s timezone. If end is earlier than
                start, the window wraps past midnight — e.g. 18 → 9 means 18:00 today through 09:00 the next day.
                Leave both empty for no restriction.
              </p>

              <FormField
                control={form.control}
                name="copy_window_weekend_unrestricted"
                render={({ field }) => (
                  <FormItem className="flex items-center justify-between rounded-md border p-3">
                    <div>
                      <FormLabel className="mb-0">Unrestricted on weekends</FormLabel>
                      <FormDescription>Ignore the copy window entirely on Saturday/Sunday (job&apos;s timezone).</FormDescription>
                    </div>
                    <FormControl>
                      <Switch checked={field.value} onCheckedChange={field.onChange} />
                    </FormControl>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="local_backup_path_pattern"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Local backup path pattern (optional)</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormDescription>Soft consistency check only, independent of verification.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="sql_instance_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>SQL instance (verification)</FormLabel>
                    <Select value={field.value} onValueChange={(v) => v && field.onChange(v)}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={NONE}>None (no verification)</SelectItem>
                        {sqlInstancesQuery.data?.items.map((si) => (
                          <SelectItem key={si.id} value={String(si.id)}>
                            {si.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {form.watch("sql_instance_id") !== NONE ? (
                <>
                  <FormField
                    control={form.control}
                    name="database_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Database name</FormLabel>
                        <FormControl>
                          <Input {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="verification_method"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Verification method</FormLabel>
                        <FormControl>
                          <Input placeholder="RESTORE_VERIFYONLY" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              ) : null}

              <FormField
                control={form.control}
                name="is_enabled"
                render={({ field }) => (
                  <FormItem className="flex items-center justify-between rounded-md border p-3">
                    <FormLabel className="mb-0">Enabled</FormLabel>
                    <FormControl>
                      <Switch checked={field.value} onCheckedChange={field.onChange} />
                    </FormControl>
                  </FormItem>
                )}
              />

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => navigate(-1)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={mutation.isPending}>
                  {mutation.isPending ? "Saving…" : mode === "create" ? "Create job" : "Save changes"}
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
