import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RestorePage } from "@/pages/RestorePage";
import { renderWithProviders } from "@/test/renderWithProviders";
import { paginated, makeSqlInstance, makeUser } from "@/test/fixtures";

vi.mock("@/auth/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("@/api/endpoints/sqlInstances", () => ({ listSqlInstances: vi.fn() }));
vi.mock("@/api/endpoints/backupJobs", () => ({ listBackupJobs: vi.fn() }));
vi.mock("@/api/endpoints/backupRecords", () => ({ listBackupRecords: vi.fn(), getBackupRecord: vi.fn() }));
vi.mock("@/api/endpoints/restoreOperations", () => ({
  listRestoreOperations: vi.fn(),
  getRestoreOperationLog: vi.fn(),
  createRestoreOperation: vi.fn(),
  updateRestoreOperation: vi.fn(),
}));

import { useAuth } from "@/auth/AuthContext";
import { listSqlInstances } from "@/api/endpoints/sqlInstances";
import { listBackupJobs } from "@/api/endpoints/backupJobs";
import { listBackupRecords, getBackupRecord } from "@/api/endpoints/backupRecords";
import { createRestoreOperation, listRestoreOperations } from "@/api/endpoints/restoreOperations";

const sqlInstance = makeSqlInstance({ id: 1, name: "SQLINST1" });

function setAuth(overrides: Partial<ReturnType<typeof useAuth>>) {
  vi.mocked(useAuth).mockReturnValue({
    token: "tok",
    user: makeUser(),
    isLoading: false,
    isAdmin: false,
    login: vi.fn(),
    logout: vi.fn(),
    ...overrides,
  } as ReturnType<typeof useAuth>);
}

/** Opens a Radix Select trigger and picks the option with the given text. */
async function chooseOption(user: ReturnType<typeof userEvent.setup>, trigger: HTMLElement, optionText: string) {
  await user.click(trigger);
  const option = await screen.findByRole("option", { name: optionText });
  await user.click(option);
}

function getNewRestoreCard(): HTMLElement {
  const title = screen.getByText("New restore");
  const card = title.closest(".rounded-xl");
  if (!card) throw new Error("Could not find the 'New restore' card container");
  return card as HTMLElement;
}

/**
 * These form fields use a plain <label> with no `for`/`id` association (not
 * a real accessibility issue here since the wrapping `<div>` visually groups
 * them, but it does mean `getByLabelText` can't find them) -- so look up the
 * input that's a sibling of the label with the given text instead.
 */
function inputNearLabel(card: HTMLElement, labelText: string): HTMLInputElement {
  const label = within(card).getByText(labelText);
  const input = label.parentElement?.querySelector("input");
  if (!input) throw new Error(`No <input> found next to label "${labelText}"`);
  return input as HTMLInputElement;
}

beforeEach(() => {
  vi.mocked(listSqlInstances).mockResolvedValue(paginated([sqlInstance]));
  vi.mocked(listBackupJobs).mockResolvedValue(paginated([]));
  vi.mocked(listBackupRecords).mockResolvedValue(paginated([]));
  vi.mocked(getBackupRecord).mockResolvedValue({
    id: 5,
    backup_job_id: 1,
    file_name: "backup.bak",
    remote_path: "/backups/backup.bak",
  } as never);
  vi.mocked(listRestoreOperations).mockResolvedValue(paginated([]));
  vi.mocked(createRestoreOperation).mockResolvedValue({ id: 99 } as never);
});

describe("RestorePage -- operator role", () => {
  beforeEach(() => setAuth({ isAdmin: false, user: makeUser({ role: "OPERATOR" }) }));

  it("does not render the mode select at all, and shows the MISSING-only explanation instead", async () => {
    renderWithProviders(<RestorePage />, { route: "/restore" });

    await waitFor(() => expect(listSqlInstances).toHaveBeenCalled());
    const card = getNewRestoreCard();

    expect(within(card).queryByText("Mode")).not.toBeInTheDocument();
    expect(within(card).getAllByRole("combobox")).toHaveLength(1); // only the SQL instance select
    expect(
      within(card).getByText(/Operators can only request MISSING-mode restores/),
    ).toBeInTheDocument();
  });

  it("keeps the submit button disabled until confirmation_database_name exactly matches database_name, then submits with mode hardcoded to MISSING", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RestorePage />, { route: "/restore" });

    await waitFor(() => expect(listSqlInstances).toHaveBeenCalled());
    const card = getNewRestoreCard();

    const submitButton = within(card).getByRole("button", { name: "Request restore" });
    expect(submitButton).toBeDisabled();

    const sqlInstanceTrigger = within(card).getAllByRole("combobox")[0];
    await chooseOption(user, sqlInstanceTrigger, "SQLINST1");

    const backupRecordIdInput = inputNearLabel(card, "Backup record ID");
    await user.type(backupRecordIdInput, "5");

    const dbNameInput = inputNearLabel(card, "Database name");
    await user.type(dbNameInput, "ProdDB");
    expect(submitButton).toBeDisabled();

    const confirmInput = inputNearLabel(card, "Confirm database name");
    await user.type(confirmInput, "prod-typo");
    expect(submitButton).toBeDisabled();
    expect(within(card).getByText("Must exactly match the database name above.")).toBeInTheDocument();

    await user.clear(confirmInput);
    await user.type(confirmInput, "ProdDB");
    expect(submitButton).toBeEnabled();

    await user.click(submitButton);

    await waitFor(() => expect(createRestoreOperation).toHaveBeenCalled());
    expect(createRestoreOperation).toHaveBeenCalledWith(
      "tok",
      expect.objectContaining({
        backup_record_id: 5,
        sql_instance_id: 1,
        database_name: "ProdDB",
        confirmation_database_name: "ProdDB",
        mode: "MISSING",
      }),
    );
  });
});

describe("RestorePage -- admin role", () => {
  beforeEach(() => setAuth({ isAdmin: true, user: makeUser({ role: "ADMIN" }) }));

  it("renders the full mode select with ALL / EXISTING / MISSING options", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RestorePage />, { route: "/restore" });

    await waitFor(() => expect(listSqlInstances).toHaveBeenCalled());
    const card = getNewRestoreCard();

    expect(within(card).getByText("Mode")).toBeInTheDocument();
    const comboboxes = within(card).getAllByRole("combobox");
    // SQL instance, Backup job, Backup record, Mode.
    expect(comboboxes).toHaveLength(4);

    const modeTrigger = comboboxes[3];
    await user.click(modeTrigger);
    expect(await screen.findByRole("option", { name: "ALL" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "EXISTING" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "MISSING" })).toBeInTheDocument();
  });
});
