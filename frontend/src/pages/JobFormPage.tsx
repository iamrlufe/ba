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
    name: z.string().min(1, "Обязательное поле").max(255),
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
    remote_directory_override: z.string().max(500).optional(),
    server_id: z.string().min(1, "Обязательное поле"),
    disk_id: z.string().min(1, "Обязательное поле"),
    sql_instance_id: z.string(),
    is_enabled: z.boolean(),
  })
  .superRefine((data, ctx) => {
    if (data.sql_instance_id !== NONE && data.sql_instance_id !== "") {
      if (!data.verification_method) {
        ctx.addIssue({
          code: "custom",
          message: "Обязательно при выбранном экземпляре SQL",
          path: ["verification_method"],
        });
      }
      if (!data.database_name) {
        ctx.addIssue({
          code: "custom",
          message: "Обязательно при выбранном экземпляре SQL (нужно для запросов верификации msdb)",
          path: ["database_name"],
        });
      }
    }

    // --- trigger_mode cross-field rules ---
    if (data.trigger_mode === "SCHEDULE") {
      if (!data.source_path) {
        ctx.addIssue({ code: "custom", message: "Обязательно для задач по расписанию", path: ["source_path"] });
      }
      if (!data.schedule_cron) {
        ctx.addIssue({ code: "custom", message: "Обязательно для задач по расписанию", path: ["schedule_cron"] });
      }
    } else if (data.trigger_mode === "WATCH") {
      if (!data.watch_directory) {
        ctx.addIssue({ code: "custom", message: "Обязательно для задач в режиме наблюдения", path: ["watch_directory"] });
      }
      if (data.backup_type === "TRANSACTION_LOG" || data.backup_type === "CUSTOM") {
        ctx.addIssue({
          code: "custom",
          message:
            "Задачи в режиме наблюдения не поддерживают типы бэкапа Transaction Log или Custom (последовательные/накопительные бэкапы не могут безопасно использовать семантику передачи «последний файл побеждает»)",
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
        message: "Укажите оба часа — начало и конец — или оставьте оба поля пустыми",
        path: [startSet ? "copy_window_end_hour" : "copy_window_start_hour"],
      });
    } else if (startSet && endSet && data.copy_window_start_hour === data.copy_window_end_hour) {
      ctx.addIssue({
        code: "custom",
        message: "Часы начала и конца должны отличаться (равные значения — не окно)",
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
  remote_directory_override: "",
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
  const watchedOverride = form.watch("remote_directory_override");

  const effectivePreview = useMemo(() => {
    if (watchedOverride?.trim()) {
      return { value: watchedOverride, isLive: true };
    }
    if (jobQuery.data?.remote_directory) {
      return { value: jobQuery.data.remote_directory, isLive: false };
    }
    return null;
  }, [watchedOverride, jobQuery.data?.remote_directory]);

  const nextRunPreview = useMemo(() => {
    if (!watchedScheduleCron) return "Введите расписание для предпросмотра следующего запуска";
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
      return `Следующий запуск: ${local} (${watchedTimezone}) = ${utc} UTC`;
    } catch {
      return "Некорректное расписание или часовой пояс";
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
        remote_directory_override: job.remote_directory_override ?? "",
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
        // Not yet exposed as a form field (out of scope of the current spec);
        // pinned to the backend's own documented default (see
        // BackupJobCreate.pending_to_running_grace_minutes) so job creation
        // keeps compiling/working against the live schema. Needs a real form
        // field + spec before this should be considered done.
        pending_to_running_grace_minutes: 30,
        copy_window_start_hour: values.copy_window_start_hour ?? null,
        copy_window_end_hour: values.copy_window_end_hour ?? null,
        copy_window_weekend_unrestricted: values.copy_window_weekend_unrestricted,
        local_backup_path_pattern: values.local_backup_path_pattern || null,
        remote_directory_override: values.remote_directory_override || null,
        server_id: Number(values.server_id),
        disk_id: Number(values.disk_id),
        sql_instance_id: values.sql_instance_id === NONE ? null : Number(values.sql_instance_id),
        is_enabled: values.is_enabled,
      }),
    onSuccess: (job) => {
      toast.success("Задача резервного копирования создана");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.all() });
      navigate(`/jobs/${job.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Не удалось создать задачу резервного копирования");
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
      if (dirty.remote_directory_override) {
        patch.remote_directory_override = values.remote_directory_override || null;
      }
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
      toast.success("Задача резервного копирования обновлена");
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.detail(job.id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.backupJobs.all() });
      navigate(`/jobs/${job.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.detail : "Не удалось обновить задачу резервного копирования");
    },
  });

  const mutation = mode === "create" ? createMutation : updateMutation;

  if (mode === "edit" && jobQuery.isLoading) return <CardSkeleton />;
  if (mode === "edit" && jobQuery.isError) {
    return <ErrorState error={jobQuery.error} onRetry={() => jobQuery.refetch()} backTo="/jobs" />;
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-2xl font-semibold">{mode === "create" ? "Новая задача резервного копирования" : "Редактирование задачи резервного копирования"}</h1>
      <Card>
        <CardHeader>
          <CardTitle>Параметры задачи</CardTitle>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form className="space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))}>
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Название</FormLabel>
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
                    <FormLabel>Сервер</FormLabel>
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
                          <SelectValue placeholder="Выберите сервер" />
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
                    {mode === "edit" ? <FormDescription>Сервер нельзя изменить после создания.</FormDescription> : null}
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="disk_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Диск</FormLabel>
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
                          <SelectValue placeholder={serverId ? "Выберите диск" : "Сначала выберите сервер"} />
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
                    {mode === "edit" ? <FormDescription>Диск нельзя изменить после создания.</FormDescription> : null}
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="trigger_mode"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Способ запуска</FormLabel>
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
                        <SelectItem value="SCHEDULE">Расписание (cron)</SelectItem>
                        <SelectItem value="WATCH">Наблюдение за директорией</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      Режим наблюдения недоступен для типов бэкапа Transaction Log или Custom.
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
                      <FormLabel>Путь источника (удалённый)</FormLabel>
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
                      <FormLabel>Директория наблюдения (удалённая)</FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormDescription>
                        Директория, за которой агент наблюдает на предмет новых/изменённых файлов, а не путь к одному файлу.
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
                      <FormLabel>Тип бэкапа</FormLabel>
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
                          Расписание (cron)
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

              <FormField
                control={form.control}
                name="remote_directory_override"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Директория на FTP-назначении (переопределение)</FormLabel>
                    <FormControl>
                      <Input placeholder="F:\ftp\Taraz\tTaraz\DIFF\" {...field} />
                    </FormControl>
                    <FormDescription>
                      Если оставить пустым, директория на FTP будет определена автоматически — по имени сервера, названию задачи и типу бэкапа.
                    </FormDescription>
                    {mode === "edit" && effectivePreview ? (
                      <p className="text-xs text-muted-foreground">
                        {effectivePreview.isLive ? (
                          "Будет использована указанная выше директория."
                        ) : (
                          <>
                            Текущая директория на сервере (на момент открытия страницы):{" "}
                            <span className="font-mono">{effectivePreview.value}</span>
                          </>
                        )}
                      </p>
                    ) : null}
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="grid grid-cols-3 gap-4">
                <FormField
                  control={form.control}
                  name="timezone"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Часовой пояс</FormLabel>
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
                      <FormLabel>Хранение (дней)</FormLabel>
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
                      <FormLabel>Мин. число копий для хранения</FormLabel>
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
                      <FormLabel>Ожидаемая макс. длительность (минут)</FormLabel>
                      <FormControl>
                        <Input type="number" placeholder="необязательно" {...field} />
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
                      <FormLabel>Допуск на пропущенный запуск (минут)</FormLabel>
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
                      <FormLabel>Начало окна копирования (час, 0-23)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={0}
                          max={23}
                          placeholder="необязательно"
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
                      <FormLabel>Конец окна копирования (час, 0-23)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={0}
                          max={23}
                          placeholder="необязательно"
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
                Ограничивает, когда агент может передавать файл, в часовом поясе задачи. Если конец раньше начала,
                окно продолжается за полночь — например, 18 → 9 означает с 18:00 сегодня до 09:00 следующего дня.
                Оставьте оба поля пустыми, чтобы снять ограничение.
              </p>

              <FormField
                control={form.control}
                name="copy_window_weekend_unrestricted"
                render={({ field }) => (
                  <FormItem className="flex items-center justify-between rounded-md border p-3">
                    <div>
                      <FormLabel className="mb-0">Без ограничений по выходным</FormLabel>
                      <FormDescription>Полностью игнорировать окно копирования по субботам/воскресеньям (в часовом поясе задачи).</FormDescription>
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
                    <FormLabel>Шаблон локального пути бэкапа (необязательно)</FormLabel>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormDescription>Только мягкая проверка согласованности, не связана с верификацией.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="sql_instance_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Экземпляр SQL (верификация)</FormLabel>
                    <Select value={field.value} onValueChange={(v) => v && field.onChange(v)}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value={NONE}>Нет (без верификации)</SelectItem>
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
                        <FormLabel>Имя базы данных</FormLabel>
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
                        <FormLabel>Метод верификации</FormLabel>
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
                    <FormLabel className="mb-0">Включена</FormLabel>
                    <FormControl>
                      <Switch checked={field.value} onCheckedChange={field.onChange} />
                    </FormControl>
                  </FormItem>
                )}
              />

              <div className="flex justify-end gap-2">
                <Button type="button" variant="outline" onClick={() => navigate(-1)}>
                  Отмена
                </Button>
                <Button type="submit" disabled={mutation.isPending}>
                  {mutation.isPending ? "Сохранение…" : mode === "create" ? "Создать задачу" : "Сохранить изменения"}
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
