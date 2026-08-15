import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { JobFormPage } from "@/pages/JobFormPage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeBackupJob, makeServer, makeSqlInstance, makeUser } from "@/test/fixtures";
import type { DiskRead } from "@/api/types";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/servers", () => ({ listServers: vi.fn() }));
vi.mock("@/api/endpoints/disks", () => ({ listDisks: vi.fn() }));
vi.mock("@/api/endpoints/sqlInstances", () => ({ listSqlInstances: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({
  createBackupJob: vi.fn(),
  getBackupJob: vi.fn(),
  updateBackupJob: vi.fn(),
}));

import { useAuth } from "@/auth/AuthContext";
import { listServers } from "@/api/endpoints/servers";
import { listDisks } from "@/api/endpoints/disks";
import { listSqlInstances } from "@/api/endpoints/sqlInstances";
import { createBackupJob, getBackupJob, updateBackupJob } from "@/api/endpoints/backupJobs";

const server = makeServer({ id: 7, name: "prod-sql-01" });
const disk: DiskRead = {
  id: 1,
  server_id: 7,
  label: "C-drive",
  mount_path: "C:\\backups",
  warning_threshold_pct: 80,
  critical_threshold_pct: 90,
  total_bytes: null,
  free_bytes: null,
  used_pct: null,
  usage_checked_at: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function setAuth() {
  vi.mocked(useAuth).mockReturnValue({
    token: "tok",
    user: makeUser(),
    isLoading: false,
    isAdmin: true,
    login: vi.fn(),
    logout: vi.fn(),
  } as ReturnType<typeof useAuth>);
}

/** Opens a Radix Select trigger (found by its associated label text) and picks an option. */
async function chooseOption(user: ReturnType<typeof userEvent.setup>, labelText: string, optionText: string) {
  const trigger = screen.getByLabelText(labelText);
  await user.click(trigger);
  const option = await screen.findByRole("option", { name: optionText });
  await user.click(option);
}

/** Fills in name + server + disk -- the fields required for any submission in create mode. */
async function fillCoreRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Название"), "my-job");
  await chooseOption(user, "Сервер", "prod-sql-01");
  await waitFor(() => expect(listDisks).toHaveBeenCalled());
  await chooseOption(user, "Диск", "C-drive (C:\\backups)");
}

/**
 * Finds an element whose own textContent (but none of its direct children's
 * textContent) matches `regex` -- for asserting on JSX text that is broken up
 * across sibling text nodes and inline elements (e.g. a label followed by a
 * `<span className="font-mono">`), which plain `getByText(string)` can't
 * match since it only matches nodes that are themselves a single contiguous
 * text node.
 */
function getByTextAcrossNodes(regex: RegExp) {
  return screen.getByText((_content, element) => {
    if (!element) return false;
    const hasText = (el: Element) => regex.test(el.textContent ?? "");
    const elementHasText = hasText(element);
    const childrenDontHaveText = Array.from(element.children).every((child) => !hasText(child));
    return elementHasText && childrenDontHaveText;
  });
}

beforeEach(() => {
  setAuth();
  vi.mocked(listServers).mockResolvedValue(paginated([server]));
  vi.mocked(listDisks).mockResolvedValue(paginated([disk]));
  vi.mocked(listSqlInstances).mockResolvedValue(paginated([makeSqlInstance()]));
});

describe("JobFormPage -- create mode, trigger_mode conditional rendering", () => {
  it("defaults to SCHEDULE mode: shows source_path/schedule_cron, hides watch_directory", async () => {
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());

    expect(screen.getByLabelText("Путь источника (удалённый)")).toBeInTheDocument();
    expect(screen.getByLabelText("Расписание (cron)")).toBeInTheDocument();
    expect(screen.queryByLabelText("Директория наблюдения (удалённая)")).not.toBeInTheDocument();
  });

  it("switching to WATCH hides source_path/schedule_cron and shows watch_directory", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await chooseOption(user, "Способ запуска", "Наблюдение за директорией");

    expect(screen.queryByLabelText("Путь источника (удалённый)")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Расписание (cron)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Директория наблюдения (удалённая)")).toBeInTheDocument();
  });
});

describe("JobFormPage -- create mode, WATCH + TRANSACTION_LOG/CUSTOM rejected", () => {
  it("blocks submission and does not call createBackupJob", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillCoreRequiredFields(user);

    await chooseOption(user, "Способ запуска", "Наблюдение за директорией");
    await user.type(screen.getByLabelText("Директория наблюдения (удалённая)"), "/watched/incoming");
    await chooseOption(user, "Тип бэкапа", "TRANSACTION_LOG");

    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    expect(
      await screen.findByText(/Задачи в режиме наблюдения не поддерживают типы бэкапа Transaction Log или Custom/),
    ).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });
});

describe("JobFormPage -- edit mode, trigger_mode switch submits coherent field set", () => {
  it("SCHEDULE -> WATCH: patch includes trigger_mode, watch_directory, source_path:null, schedule_cron:null together", async () => {
    const job = makeBackupJob({
      id: 5,
      trigger_mode: "SCHEDULE",
      source_path: "/remote/backups",
      schedule_cron: "0 2 * * *",
      watch_directory: null,
      server_id: 7,
      disk_id: 1,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/5", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveValue(job.name));
    await waitFor(() => expect(screen.getByLabelText("Путь источника (удалённый)")).toHaveValue(job.source_path));

    await chooseOption(user, "Способ запуска", "Наблюдение за директорией");
    await user.type(screen.getByLabelText("Директория наблюдения (удалённая)"), "/watched/incoming");

    await user.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    expect(patch).toEqual(
      expect.objectContaining({
        trigger_mode: "WATCH",
        watch_directory: "/watched/incoming",
        source_path: null,
        schedule_cron: null,
      }),
    );
  });

  it("WATCH -> SCHEDULE: patch includes trigger_mode, source_path, schedule_cron, watch_directory:null together", async () => {
    const job = makeBackupJob({
      id: 6,
      trigger_mode: "WATCH",
      source_path: null,
      schedule_cron: null,
      watch_directory: "/watched/existing",
      server_id: 7,
      disk_id: 1,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/6", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveValue(job.name));
    await waitFor(() => expect(screen.getByLabelText("Директория наблюдения (удалённая)")).toHaveValue(job.watch_directory));

    await chooseOption(user, "Способ запуска", "Расписание (cron)");
    await user.type(screen.getByLabelText("Путь источника (удалённый)"), "/remote/new-path");
    await user.type(screen.getByLabelText("Расписание (cron)"), "0 3 * * *");

    await user.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    expect(patch).toEqual(
      expect.objectContaining({
        trigger_mode: "SCHEDULE",
        source_path: "/remote/new-path",
        schedule_cron: "0 3 * * *",
        watch_directory: null,
      }),
    );
  });
});

describe("JobFormPage -- copy-window validation", () => {
  async function fillScheduleModeCore(user: ReturnType<typeof userEvent.setup>) {
    await fillCoreRequiredFields(user);
    await user.type(screen.getByLabelText("Путь источника (удалённый)"), "/remote/backups");
    await user.type(screen.getByLabelText("Расписание (cron)"), "0 2 * * *");
  }

  it("only start hour set (end empty) shows a validation error and blocks submission", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Начало окна копирования (час, 0-23)"), "10");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    expect(await screen.findByText(/Укажите оба часа — начало и конец — или оставьте оба поля пустыми/)).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });

  it("equal start/end hour shows a validation error and blocks submission", async () => {
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Начало окна копирования (час, 0-23)"), "5");
    await user.type(screen.getByLabelText("Конец окна копирования (час, 0-23)"), "5");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    expect(
      await screen.findByText(/Часы начала и конца должны отличаться/),
    ).toBeInTheDocument();
    expect(createBackupJob).not.toHaveBeenCalled();
  });

  it("wraparound window (start > end, e.g. 18 -> 9) is valid and submission proceeds", async () => {
    vi.mocked(createBackupJob).mockResolvedValue(makeBackupJob({ id: 99 }));
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.type(screen.getByLabelText("Начало окна копирования (час, 0-23)"), "18");
    await user.type(screen.getByLabelText("Конец окна копирования (час, 0-23)"), "9");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    await waitFor(() => expect(createBackupJob).toHaveBeenCalled());
  });
});

describe("JobFormPage -- remote_directory_override, create mode payload", () => {
  async function fillScheduleModeCore(user: ReturnType<typeof userEvent.setup>) {
    await fillCoreRequiredFields(user);
    await user.type(screen.getByLabelText("Путь источника (удалённый)"), "/remote/backups");
    await user.type(screen.getByLabelText("Расписание (cron)"), "0 2 * * *");
  }

  it("submits the entered FTP override string in the create payload", async () => {
    vi.mocked(createBackupJob).mockResolvedValue(makeBackupJob({ id: 99 }));
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);
    await user.type(
      screen.getByLabelText("Директория на FTP-назначении (переопределение)"),
      "Taraz/tTaraz/DIFF/",
    );

    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    await waitFor(() => expect(createBackupJob).toHaveBeenCalled());
    const [, payload] = vi.mocked(createBackupJob).mock.calls[0];
    expect(payload).toHaveProperty("remote_directory_override", "Taraz/tTaraz/DIFF/");
  });

  it("leaving the FTP override field empty sends null, not an empty string or a missing key", async () => {
    vi.mocked(createBackupJob).mockResolvedValue(makeBackupJob({ id: 100 }));
    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="create" />, { route: "/jobs/new" });

    await waitFor(() => expect(listServers).toHaveBeenCalled());
    await fillScheduleModeCore(user);

    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    await waitFor(() => expect(createBackupJob).toHaveBeenCalled());
    const [, payload] = vi.mocked(createBackupJob).mock.calls[0];
    expect(payload).toHaveProperty("remote_directory_override", null);
  });
});

describe("JobFormPage -- remote_directory_override live preview (edit mode)", () => {
  function editJob(overrides: Parameters<typeof makeBackupJob>[0] = {}) {
    return makeBackupJob({
      id: 8,
      server_id: 7,
      disk_id: 1,
      remote_directory: "trz1c8.rcku.net/Nightly_42/DIFF/",
      remote_directory_override: null,
      ...overrides,
    });
  }

  it("shows the server-resolved directory with the 'computed at page load' caption before any edit", async () => {
    const job = editJob();
    vi.mocked(getBackupJob).mockResolvedValue(job);

    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/8", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveValue(job.name));

    expect(screen.getByText(job.remote_directory!)).toBeInTheDocument();
    expect(getByTextAcrossNodes(/Текущая директория на сервере/)).toBeInTheDocument();
    expect(screen.queryByText("Будет использована указанная выше директория.")).not.toBeInTheDocument();
  });

  it("switches to the 'will use the entered directory' caption as soon as the override field is typed into", async () => {
    const job = editJob();
    vi.mocked(getBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/8", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveValue(job.name));
    expect(screen.getByText(job.remote_directory!)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Директория на FTP-назначении (переопределение)"), "Custom/Path/");

    expect(await screen.findByText("Будет использована указанная выше директория.")).toBeInTheDocument();
    expect(screen.queryByText(job.remote_directory!)).not.toBeInTheDocument();
  });
});

describe("JobFormPage -- remote_directory_override dirty-field PATCH semantics", () => {
  it("leaving the FTP override field untouched omits the key from the PATCH payload entirely", async () => {
    const job = makeBackupJob({
      id: 9,
      server_id: 7,
      disk_id: 1,
      remote_directory: "trz1c8.rcku.net/Nightly_42/DIFF/",
      remote_directory_override: null,
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/9", path: "/jobs/:id" });

    await waitFor(() => expect(screen.getByLabelText("Название")).toHaveValue(job.name));
    await user.type(screen.getByLabelText("Название"), "-renamed");

    await user.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    expect(patch).not.toHaveProperty("remote_directory_override");
    expect(patch).toHaveProperty("name", `${job.name}-renamed`);
  });

  it("typing an override then reverting it back to the original value -- documents the actual dirty-tracking result", async () => {
    const job = makeBackupJob({
      id: 10,
      server_id: 7,
      disk_id: 1,
      remote_directory: "trz1c8.rcku.net/Nightly_42/DIFF/",
      remote_directory_override: "Existing/Override/",
    });
    vi.mocked(getBackupJob).mockResolvedValue(job);
    vi.mocked(updateBackupJob).mockResolvedValue(job);

    const user = userEvent.setup();
    renderWithProviders(<JobFormPage mode="edit" />, { route: "/jobs/10", path: "/jobs/:id" });

    await waitFor(() =>
      expect(screen.getByLabelText("Директория на FTP-назначении (переопределение)")).toHaveValue(
        job.remote_directory_override,
      ),
    );

    const overrideField = screen.getByLabelText("Директория на FTP-назначении (переопределение)");
    await user.clear(overrideField);
    await user.type(overrideField, "Existing/Override/");
    expect(overrideField).toHaveValue(job.remote_directory_override);

    // Also touch an unrelated field so the mutation always has something
    // dirty to submit, isolating this assertion to remote_directory_override.
    await user.type(screen.getByLabelText("Название"), "-touched");

    await user.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() => expect(updateBackupJob).toHaveBeenCalled());
    const [, , patch] = vi.mocked(updateBackupJob).mock.calls[0];
    // react-hook-form recomputes `dirtyFields` against `defaultValues` on
    // every change, so a field that's cleared and retyped back to its
    // original value is no longer considered dirty -- the key is correctly
    // omitted, same as if it had never been touched.
    expect(patch).not.toHaveProperty("remote_directory_override");
  });
});
