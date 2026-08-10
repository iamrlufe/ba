/**
 * Centralized TanStack Query key factory. Every query/mutation invalidation
 * in the app goes through these factories -- no ad hoc string keys.
 */

export const queryKeys = {
  auth: {
    me: () => ["auth", "me"] as const,
  },
  servers: {
    all: () => ["servers"] as const,
    list: (filters: Record<string, unknown>) => ["servers", "list", filters] as const,
    detail: (id: number) => ["servers", "detail", id] as const,
  },
  disks: {
    all: () => ["disks"] as const,
    list: (filters: Record<string, unknown>) => ["disks", "list", filters] as const,
    detail: (id: number) => ["disks", "detail", id] as const,
  },
  backupJobs: {
    all: () => ["backupJobs"] as const,
    list: (filters: Record<string, unknown>) => ["backupJobs", "list", filters] as const,
    detail: (id: number) => ["backupJobs", "detail", id] as const,
  },
  sqlInstances: {
    all: () => ["sqlInstances"] as const,
    list: (filters: Record<string, unknown>) => ["sqlInstances", "list", filters] as const,
    detail: (id: number) => ["sqlInstances", "detail", id] as const,
  },
  jobRuns: {
    all: () => ["jobRuns"] as const,
    list: (filters: Record<string, unknown>) => ["jobRuns", "list", filters] as const,
    detail: (id: number) => ["jobRuns", "detail", id] as const,
    log: (id: number) => ["jobRuns", "log", id] as const,
  },
  verificationRuns: {
    all: (backupJobId: number) => ["verificationRuns", backupJobId] as const,
    list: (backupJobId: number, filters: Record<string, unknown>) =>
      ["verificationRuns", backupJobId, "list", filters] as const,
    detail: (backupJobId: number, runId: number) => ["verificationRuns", backupJobId, "detail", runId] as const,
  },
  backupRecords: {
    all: () => ["backupRecords"] as const,
    list: (filters: Record<string, unknown>) => ["backupRecords", "list", filters] as const,
    detail: (id: number) => ["backupRecords", "detail", id] as const,
  },
  restoreOperations: {
    all: () => ["restoreOperations"] as const,
    list: (filters: Record<string, unknown>) => ["restoreOperations", "list", filters] as const,
    detail: (id: number) => ["restoreOperations", "detail", id] as const,
    log: (id: number) => ["restoreOperations", "log", id] as const,
  },
  alerts: {
    all: () => ["alerts"] as const,
    list: (filters: Record<string, unknown>) => ["alerts", "list", filters] as const,
  },
  summary: {
    daily: () => ["summary", "daily"] as const,
  },
} as const;
