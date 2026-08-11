import type {
  JobRunRead,
  PaginatedResponse,
  RestoreOperationRead,
  ServerRead,
  SqlInstanceRead,
  UserRead,
} from "@/api/types";

export function paginated<T>(items: T[], overrides: Partial<PaginatedResponse<T>> = {}): PaginatedResponse<T> {
  return { items, total: items.length, limit: 20, offset: 0, ...overrides };
}

export function makeUser(overrides: Partial<UserRead> = {}): UserRead {
  return {
    id: 1,
    username: "admin",
    role: "ADMIN",
    is_active: true,
    telegram_user_id: null,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as UserRead;
}

export function makeServer(overrides: Partial<ServerRead> = {}): ServerRead {
  return {
    id: 7,
    name: "prod-sql-01",
    host: "10.0.0.5",
    port: 22,
    protocol: "SFTP",
    notes: null,
    status: "ACTIVE",
    credentials_set: true,
    ssh_key_set: false,
    last_seen_at: "2026-08-10T10:00:00Z",
    is_deleted: false,
    monitored_service_names: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as ServerRead;
}

export function makeSqlInstance(overrides: Partial<SqlInstanceRead> = {}): SqlInstanceRead {
  return {
    id: 3,
    name: "SQL1",
    host: "10.0.0.6",
    port: 1433,
    instance_name: null,
    use_windows_auth: false,
    notes: null,
    server_id: null,
    credentials_set: true,
    status: "ACTIVE",
    last_verified_connection_at: null,
    is_deleted: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as SqlInstanceRead;
}

export function makeJobRun(overrides: Partial<JobRunRead> = {}): JobRunRead {
  return {
    id: 42,
    backup_job_id: 1,
    status: "RUNNING",
    triggered_by: "scheduler",
    started_at: "2026-08-10T10:00:00Z",
    finished_at: null,
    file_path: null,
    file_size_bytes: null,
    duration_seconds: null,
    verification_status: "PENDING",
    verification_details: null,
    error_message: null,
    percent: 10,
    current_file: "file_0001.bak",
    bytes_done: 1024,
    created_at: "2026-08-10T10:00:00Z",
    ...overrides,
  } as JobRunRead;
}

export function makeRestoreOperation(overrides: Partial<RestoreOperationRead> = {}): RestoreOperationRead {
  return {
    id: 1,
    backup_record_id: 5,
    sql_instance_id: 3,
    database_name: "ProdDB",
    confirmation_database_name: "ProdDB",
    mode: "MISSING",
    status: "PENDING",
    requested_by: "operator1",
    requested_by_channel: "WEB",
    requested_at: "2026-08-10T10:00:00Z",
    started_at: null,
    finished_at: null,
    error_message: null,
    ...overrides,
  } as RestoreOperationRead;
}
