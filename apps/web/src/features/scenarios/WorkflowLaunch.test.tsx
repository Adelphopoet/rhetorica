import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createConversation } from "@/features/chat/historyApi";
import { deleteStagedFile, uploadFile, type FileArtifactRef } from "@/features/files/fileApi";
import { LaunchForm } from "./WorkflowLaunch";
import { startWorkflow, type Version } from "./workflowApi";

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/features/chat/historyApi", () => ({ createConversation: vi.fn() }));
vi.mock("@/features/files/fileApi", async original => ({
  ...await original<typeof import("@/features/files/fileApi")>(),
  deleteStagedFile: vi.fn(),
  uploadFile: vi.fn(),
}));
vi.mock("./workflowApi", async original => ({
  ...await original<typeof import("./workflowApi")>(),
  startWorkflow: vi.fn(),
}));

const artifact = (id: string, filename: string): FileArtifactRef => ({
  id,
  filename,
  media_type: "text/csv",
  size_bytes: 12,
  kind: "user_upload",
  sha256: id,
});

const version: Version = {
  id: "version",
  version: 3,
  kind: "workflow",
  created_at: "2026-09-08T00:00:00Z",
  workflow: {
    schema_version: 1,
    parameters: {
      source: { type: "file", title: "Исходный файл", required: true },
      attachments: { type: "files", title: "Дополнительные файлы", required: false },
    },
    nodes: [{ id: "result", type: "result", title: "Обработка файлов", inputs: {}, outputs: {} }],
    edges: [],
    allowed_tools: [],
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(createConversation).mockResolvedValue({ id: "conversation" } as never);
  vi.mocked(deleteStagedFile).mockResolvedValue();
});

describe("scenario file parameters", () => {
  it("shares an in-flight conversation between simultaneous file uploads", async () => {
    let resolveConversation!: (value: Awaited<ReturnType<typeof createConversation>>) => void;
    vi.mocked(createConversation).mockImplementation(() => new Promise(resolve => { resolveConversation = resolve; }));
    vi.mocked(uploadFile).mockImplementation(async ({ file }) => artifact(file.name, file.name));
    vi.mocked(startWorkflow).mockResolvedValue({ run_id: "run-files" } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={version} />);
    fireEvent.change(screen.getByLabelText("Загрузить Исходный файл"), { target: { files: [new File(["1"], "one.csv")] } });
    fireEvent.change(screen.getByLabelText("Загрузить Дополнительные файлы"), { target: { files: [new File(["2"], "two.csv")] } });
    expect(createConversation).toHaveBeenCalledTimes(1);
    resolveConversation({ id: "shared-conversation" } as never);
    expect(await screen.findByText("one.csv")).toBeInTheDocument();
    expect(await screen.findByText("two.csv")).toBeInTheDocument();
    expect(uploadFile).toHaveBeenCalledTimes(2);
    expect(vi.mocked(uploadFile).mock.calls.map(([arg]) => arg.conversationId)).toEqual(["shared-conversation", "shared-conversation"]);
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await waitFor(() => expect(startWorkflow).toHaveBeenCalledWith(
      "scenario", "default", { source: artifact("one.csv", "one.csv"), attachments: [artifact("two.csv", "two.csv")] },
      { version: 3, conversation_id: "shared-conversation" }, expect.any(String),
    ));
  });

  it("retries conversation creation after a failed upload setup", async () => {
    vi.mocked(createConversation).mockRejectedValueOnce(new Error("Creation failed")).mockResolvedValueOnce({ id: "retry-conversation" } as never);
    vi.mocked(uploadFile).mockResolvedValue(artifact("retry", "retry.csv"));
    render(<LaunchForm scenarioId="scenario" workspace="default" version={version} />);
    const input = screen.getByLabelText("Загрузить Исходный файл");
    fireEvent.change(input, { target: { files: [new File(["1"], "retry.csv")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Creation failed");
    fireEvent.change(input, { target: { files: [new File(["1"], "retry.csv")] } });
    expect(await screen.findByText("retry.csv")).toBeInTheDocument();
    expect(createConversation).toHaveBeenCalledTimes(2);
    expect(uploadFile).toHaveBeenCalledWith(expect.objectContaining({ conversationId: "retry-conversation" }));
  });

  it("uploads a required file and passes its artifact to the workflow run", async () => {
    const uploaded = artifact("file-1", "stores.csv");
    vi.mocked(uploadFile).mockResolvedValue(uploaded);
    vi.mocked(startWorkflow).mockResolvedValue({ run_id: "run-1", run: { conversation_id: "conversation" } } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={version} />);

    const start = screen.getByRole("button", { name: "Запустить" });
    expect(start).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Загрузить Исходный файл"), {
      target: { files: [new File(["a,b"], "stores.csv", { type: "text/csv" })] },
    });

    expect(await screen.findByText("stores.csv")).toBeInTheDocument();
    expect(createConversation).toHaveBeenCalledExactlyOnceWith("default", "Сценарий: Обработка файлов");
    expect(uploadFile).toHaveBeenCalledWith(expect.objectContaining({ workspaceId: "default", conversationId: "conversation" }));
    expect(start).toBeEnabled();
    fireEvent.click(start);

    await waitFor(() => expect(startWorkflow).toHaveBeenCalledWith(
      "scenario",
      "default",
      { source: uploaded },
      { version: 3, conversation_id: "conversation" },
      expect.any(String),
    ));
    expect(router.push).toHaveBeenCalledWith("/scenario-runs/run-1");
  });

  it("accepts several files in one selection and removes a staged artifact", async () => {
    vi.mocked(uploadFile)
      .mockResolvedValueOnce(artifact("file-1", "one.csv"))
      .mockResolvedValueOnce(artifact("file-2", "two.csv"));
    render(<LaunchForm scenarioId="scenario" workspace="default" version={version} />);

    const input = screen.getByLabelText("Загрузить Дополнительные файлы");
    expect(input).toHaveAttribute("multiple");
    fireEvent.change(input, { target: { files: [new File(["1"], "one.csv"), new File(["2"], "two.csv")] } });

    expect(await screen.findByText("one.csv")).toBeInTheDocument();
    expect(screen.getByText("two.csv")).toBeInTheDocument();
    expect(uploadFile).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Удалить one.csv" }));
    await waitFor(() => expect(deleteStagedFile).toHaveBeenCalledWith("file-1"));
    expect(screen.queryByText("one.csv")).not.toBeInTheDocument();
    expect(screen.getByText("two.csv")).toBeInTheDocument();
  });

  it("rejects an unsupported file before creating a conversation", async () => {
    render(<LaunchForm scenarioId="scenario" workspace="default" version={version} />);
    fireEvent.change(screen.getByLabelText("Загрузить Исходный файл"), {
      target: { files: [new File(["bad"], "payload.exe")] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Неподдерживаемый формат файла");
    expect(createConversation).not.toHaveBeenCalled();
    expect(uploadFile).not.toHaveBeenCalled();
  });
});

describe("launch parameter validation", () => {
  const withParameters = (parameters: Version["workflow"]["parameters"]): Version => ({ ...version, workflow: { ...version.workflow, parameters } });

  it("preserves false and decimal values, and lets optional values be cleared", async () => {
    vi.mocked(startWorkflow).mockResolvedValue({ run_id: "run-2" } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={withParameters({ amount: { type: "number", title: "Сумма", required: false }, enabled: { type: "boolean", title: "Включён", required: false } })} />);
    fireEvent.change(screen.getByLabelText(/Сумма/), { target: { value: "1.25" } });
    fireEvent.change(screen.getByLabelText(/Включён/), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await waitFor(() => expect(startWorkflow).toHaveBeenCalledWith("scenario", "default", { amount: 1.25, enabled: false }, expect.any(Object), expect.any(String)));
  });

  it("does not turn a cleared number or boolean into zero or false", async () => {
    vi.mocked(startWorkflow).mockResolvedValue({ run_id: "run-2" } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={withParameters({ amount: { type: "number", title: "Сумма", required: false, default: 5 }, enabled: { type: "boolean", title: "Включён", required: false, default: true } })} />);
    fireEvent.change(screen.getByLabelText(/Сумма/), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText(/Включён/), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await waitFor(() => expect(startWorkflow).toHaveBeenCalledWith("scenario", "default", { amount: undefined, enabled: undefined }, expect.any(Object), expect.any(String)));
  });

  it("blocks invalid JSON and submits the latest value without requiring blur", async () => {
    vi.mocked(startWorkflow).mockResolvedValue({ run_id: "run-2" } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={withParameters({ payload: { type: "json", title: "Данные" } })} />);
    const input = screen.getByLabelText(/Данные/);
    fireEvent.change(input, { target: { value: '{"value":' } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(startWorkflow).not.toHaveBeenCalled();
    expect(input).toBeInvalid();
    fireEvent.change(input, { target: { value: '{"value":42}' } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await waitFor(() => expect(startWorkflow).toHaveBeenCalledWith("scenario", "default", { payload: { value: 42 } }, expect.any(Object), expect.any(String)));
  });

  it("keeps values after a launch failure and reuses the request key on retry", async () => {
    vi.mocked(startWorkflow).mockRejectedValueOnce(new Error("Сервер недоступен")).mockResolvedValueOnce({ run_id: "retry" } as never);
    render(<LaunchForm scenarioId="scenario" workspace="default" version={withParameters({ city: { type: "string", title: "Город" } })} />);
    fireEvent.change(screen.getByLabelText(/Город/), { target: { value: "Москва" } });
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Сервер недоступен");
    expect(screen.getByLabelText(/Город/)).toHaveValue("Москва");
    fireEvent.click(screen.getByRole("button", { name: "Запустить" }));
    await waitFor(() => expect(startWorkflow).toHaveBeenCalledTimes(2));
    expect(vi.mocked(startWorkflow).mock.calls[1][4]).toEqual(vi.mocked(startWorkflow).mock.calls[0][4]);
  });
});
