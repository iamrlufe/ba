from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import BackupType, TriggerMode
from app.schemas.alert import AlertRead
from app.schemas.common import UtcDatetime


class BackupJobBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(min_length=1, max_length=255)
    database_name: str | None = Field(default=None, max_length=255)
    # Required iff trigger_mode == SCHEDULE -- see
    # BackupJobCreate._trigger_mode_conditional_fields.
    source_path: str | None = Field(default=None, min_length=1, max_length=500)
    backup_type: BackupType = BackupType.FULL
    trigger_mode: TriggerMode = TriggerMode.SCHEDULE
    watch_directory: str | None = Field(default=None, min_length=1, max_length=500)
    # Required iff trigger_mode == SCHEDULE -- see
    # BackupJobCreate._trigger_mode_conditional_fields.
    schedule_cron: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str = Field(default="UTC", max_length=64)
    retention_days: int = Field(default=30, gt=0)
    retention_min_copies: int = Field(default=1, ge=0)
    verification_method: str | None = Field(default=None, max_length=50)
    expected_max_duration_minutes: int | None = Field(default=None, gt=0)
    missed_run_grace_minutes: int = Field(default=60, gt=0)
    pending_to_running_grace_minutes: int = Field(default=30, gt=0)
    # Copy time-window: applies to BOTH trigger modes, deferring the actual
    # FTP transfer (not detection) until the window opens. Both hours must
    # be set or both omitted, and if set they must differ -- see
    # _copy_window_valid below (and BackupJobUpdate's own copy of it).
    copy_window_start_hour: int | None = Field(default=None, ge=0, le=23)
    copy_window_end_hour: int | None = Field(default=None, ge=0, le=23)
    copy_window_weekend_unrestricted: bool = False
    # Soft, informational consistency check only -- independent of
    # sql_instance_id (not part of _verification_fields_required_when_verifying
    # below). See app/models/backup_job.py column comment /
    # app/workers/backup_verification.py for how a mismatch is surfaced.
    local_backup_path_pattern: str | None = Field(default=None, max_length=500)
    # When None (the common case), the effective remote FTP/SFTP directory
    # is computed by app.core.remote_paths.resolve_remote_directory from
    # server.name + name + id + backup_type -- see
    # app.models.backup_job.BackupJob.remote_directory. Setting this
    # overrides that computed value with a literal path. See
    # _check_remote_directory_override below for validation rules.
    remote_directory_override: str | None = Field(default=None, max_length=500)


class BackupJobCreate(BackupJobBase):
    server_id: int
    disk_id: int
    sql_instance_id: int | None = None
    is_enabled: bool = True

    @model_validator(mode="after")
    def _verification_fields_required_when_verifying(self) -> "BackupJobCreate":
        if self.sql_instance_id is not None:
            if self.verification_method is None:
                raise ValueError(
                    "verification_method is required when sql_instance_id is set "
                    "(verification is enabled iff sql_instance_id is set)"
                )
            if not self.database_name:
                raise ValueError(
                    "database_name is required when sql_instance_id is set -- "
                    "needed to query msdb.dbo.backupset for verification"
                )
        return self

    @model_validator(mode="after")
    def _trigger_mode_conditional_fields(self) -> "BackupJobCreate":
        if self.trigger_mode == TriggerMode.SCHEDULE:
            if not self.schedule_cron:
                raise ValueError("schedule_cron is required when trigger_mode is SCHEDULE")
            if not self.source_path:
                raise ValueError("source_path is required when trigger_mode is SCHEDULE")
            if self.watch_directory is not None:
                raise ValueError("watch_directory must not be set when trigger_mode is SCHEDULE")
        elif self.trigger_mode == TriggerMode.WATCH:
            if not self.watch_directory:
                raise ValueError("watch_directory is required when trigger_mode is WATCH")
            if self.schedule_cron is not None:
                raise ValueError(
                    "schedule_cron must not be set when trigger_mode is WATCH (no fixed schedule)"
                )
            if self.source_path is not None:
                raise ValueError(
                    "source_path must not be set when trigger_mode is WATCH -- use watch_directory"
                )
        return self

    @model_validator(mode="after")
    def _copy_window_valid(self) -> "BackupJobCreate":
        _check_copy_window(self.copy_window_start_hour, self.copy_window_end_hour)
        return self

    @model_validator(mode="after")
    def _cron_syntax_valid(self) -> "BackupJobCreate":
        _check_cron_syntax(self.schedule_cron)
        return self

    @model_validator(mode="after")
    def _remote_directory_override_valid(self) -> "BackupJobCreate":
        _check_remote_directory_override(self.remote_directory_override)
        return self

    @model_validator(mode="after")
    def _watch_backup_type_rejected(self) -> "BackupJobCreate":
        if self.trigger_mode == TriggerMode.WATCH and self.backup_type in (
            BackupType.TRANSACTION_LOG,
            BackupType.CUSTOM,
        ):
            raise ValueError(
                "WATCH trigger mode is not supported for TRANSACTION_LOG or CUSTOM backup "
                "types in this iteration -- sequential/cumulative or undefined-semantics "
                "backups cannot safely use latest-file-wins transfer semantics; use SCHEDULE "
                "mode instead"
            )
        return self


def _check_cron_syntax(schedule_cron: str | None) -> None:
    """Reject a `schedule_cron` that croniter itself can't parse.

    This is a NECESSARY but NOT SUFFICIENT check: it only guards against
    the case that crashed the .NET agent (an unparseable expression that
    croniter also rejects). croniter and the agent's Cronos parser are
    different cron dialects with different accepted syntax -- passing this
    check does NOT guarantee Cronos will accept the same string. Callers
    populating schedule_cron should stick to the plain 5-field
    minute/hour/day-of-month/month/day-of-week numeric-and-`*`/`,`/`-`/`/`
    syntax and avoid named day-of-week/month abbreviations (MON, JAN, ...),
    the `L`/`W`/`#`/`?` special characters, and day-of-week=7 (Cronos and
    croniter disagree on some of these).
    """
    if schedule_cron is None:
        return
    if not croniter.is_valid(schedule_cron):
        raise ValueError(
            f"schedule_cron={schedule_cron!r} is not a valid cron expression (per croniter). "
            "Note: passing this check does not guarantee the .NET agent's Cronos parser will "
            "also accept it -- they are different cron dialects. Avoid named day-of-week/month "
            "abbreviations, the L/W/#/? special characters, and day-of-week=7."
        )


def _check_copy_window(start_hour: int | None, end_hour: int | None) -> None:
    if (start_hour is None) != (end_hour is None):
        raise ValueError(
            "copy_window_start_hour and copy_window_end_hour must both be set or both be omitted"
        )
    if start_hour is not None and start_hour == end_hour:
        raise ValueError(
            "copy_window_start_hour and copy_window_end_hour must not be equal -- "
            "leave both unset for an unrestricted window"
        )


def _check_remote_directory_override(value: str | None) -> None:
    if value is None:
        return
    if value == "":
        raise ValueError(
            "remote_directory_override must not be an empty string -- omit the field "
            "entirely to use the computed default"
        )
    if value != value.strip():
        raise ValueError("remote_directory_override must not have leading/trailing whitespace")
    if any(segment == ".." for segment in value.replace("\\", "/").split("/")):
        raise ValueError("remote_directory_override must not contain '..' path-traversal segments")


class BackupJobUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    database_name: str | None = Field(default=None, max_length=255)
    source_path: str | None = Field(default=None, min_length=1, max_length=500)
    backup_type: BackupType | None = None
    trigger_mode: TriggerMode | None = None
    watch_directory: str | None = Field(default=None, min_length=1, max_length=500)
    schedule_cron: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    retention_days: int | None = Field(default=None, gt=0)
    retention_min_copies: int | None = Field(default=None, ge=0)
    verification_method: str | None = Field(default=None, max_length=50)
    expected_max_duration_minutes: int | None = Field(default=None, gt=0)
    missed_run_grace_minutes: int | None = Field(default=None, gt=0)
    pending_to_running_grace_minutes: int | None = Field(default=None, gt=0)
    copy_window_start_hour: int | None = Field(default=None, ge=0, le=23)
    copy_window_end_hour: int | None = Field(default=None, ge=0, le=23)
    copy_window_weekend_unrestricted: bool | None = None
    local_backup_path_pattern: str | None = Field(default=None, max_length=500)
    # Same "exclude_unset applies changes, explicit null clears the
    # override" convention as sql_instance_id below -- see its comment.
    remote_directory_override: str | None = Field(default=None, max_length=500)
    is_enabled: bool | None = None

    # sql_instance_id follows the "exclude_unset" pattern: if the key is
    # absent from the incoming payload, leave the stored value untouched;
    # if the key is present with value null, clear it (disable
    # verification). Callers MUST build this schema with
    # `model_validate(payload)` from a payload where absent keys are truly
    # absent (not defaulted to null), and apply changes via
    # `.model_dump(exclude_unset=True)`. The six new fields above
    # (trigger_mode, watch_directory, schedule_cron [already applied],
    # copy_window_start_hour, copy_window_end_hour,
    # copy_window_weekend_unrestricted) follow this exact same convention.
    sql_instance_id: int | None = None

    # server_id / disk_id are intentionally NOT editable here.

    @model_validator(mode="after")
    def _copy_window_valid(self) -> "BackupJobUpdate":
        # Self-contained (doesn't need current DB state) -- can be checked
        # here directly. The trigger-mode-conditional-required-fields and
        # WATCH+backup_type checks, by contrast, need the MERGED post-patch
        # state and are re-checked at the router level instead (see
        # app/routers/backup_jobs.py::update_backup_job).
        _check_copy_window(self.copy_window_start_hour, self.copy_window_end_hour)
        return self

    @model_validator(mode="after")
    def _cron_syntax_valid(self) -> "BackupJobUpdate":
        # Self-contained (doesn't need current DB state), same as
        # _copy_window_valid above -- if schedule_cron isn't in this patch
        # at all, self.schedule_cron is None and _check_cron_syntax is a
        # no-op, matching the exclude_unset convention documented on
        # sql_instance_id above.
        _check_cron_syntax(self.schedule_cron)
        return self

    @model_validator(mode="after")
    def _remote_directory_override_valid(self) -> "BackupJobUpdate":
        # Self-contained, same as _copy_window_valid/_cron_syntax_valid
        # above -- if remote_directory_override isn't in this patch at all,
        # self.remote_directory_override is None and the check is a no-op.
        _check_remote_directory_override(self.remote_directory_override)
        return self


class BackupJobRead(BackupJobBase):
    id: int
    server_id: int
    disk_id: int
    sql_instance_id: int | None
    is_enabled: bool
    last_run_at: UtcDatetime | None
    next_run_at: UtcDatetime | None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    # Non-secret SqlInstance connection info (backed by hybrid_property on
    # the ORM model), flattened onto this response so the .NET agent's
    # single jobs-poll response carries everything needed for WATCH-mode
    # msdb-priority detection (Windows Integrated Auth only -- no
    # credentials here) without a second round-trip. All None when
    # sql_instance_id is None. Callers building BackupJobRead from an ORM
    # object MUST have eager-loaded `sql_instance` first (see
    # list_agent_jobs's selectinload) -- see the hybrid_property docstrings
    # in app/models/backup_job.py.
    sql_instance_host: str | None = None
    sql_instance_port: int | None = None
    sql_instance_instance_name: str | None = None
    sql_instance_use_windows_auth: bool | None = None

    # Populated ONLY by app.routers.agents.list_agent_jobs (same convention
    # as the sql_instance_* fields above -- default None everywhere else).
    # Lets the .NET agent's single jobs-poll response tell it, per job,
    # whether there's a manual run waiting to be claimed or a cancellation
    # it needs to react to, without a second round-trip.
    pending_manual_run_id: int | None = None
    cancel_requested_run_id: int | None = None

    # Backed by the `remote_directory` hybrid_property on the ORM model
    # (app/models/backup_job.py) -- actually resolves a real value only when
    # `server` has been eager-loaded on the source BackupJob (see
    # list_backup_jobs/get_backup_job/create_backup_job/update_backup_job/
    # list_agent_jobs, all of which now eager-load or refresh it). None here
    # is just this schema's own default for construction paths that don't
    # go through a real ORM object (e.g. tests building BackupJobRead
    # directly), not a claim that the directory is unset.
    remote_directory: str | None = None


class ScheduleErrorRequest(BaseModel):
    """Reported by the .NET agent (or raised server-side, see
    app.workers.alert_worker.check_missed_runs) when its Cronos cron parser
    can't parse a SCHEDULE-mode job's schedule_cron -- see
    app.routers.backup_jobs.report_schedule_error. Mirrors
    WatchEventRequest's active/detail shape (app/schemas/watch_event.py)."""

    model_config = ConfigDict(extra="forbid")

    active: bool
    detail: str | None = Field(default=None, max_length=2000)


class ScheduleErrorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_raised: AlertRead | None = None
    alert_resolved: AlertRead | None = None
