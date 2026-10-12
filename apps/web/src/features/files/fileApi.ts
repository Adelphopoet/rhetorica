export type FileArtifactRef = {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  kind: "user_upload" | "sandbox_output" | "sql_result";
  sha256: string;
};

export type AttachmentDraft = {
  id: string;
  file: File;
  status: "selected" | "uploading" | "error";
  error?: string;
};

const ALLOWED_EXTENSIONS = new Set([
  "txt", "md", "csv", "tsv", "json", "parquet", "xlsx", "docx", "pptx",
  "pdf", "png", "jpg", "jpeg",
]);
export const MAX_INPUT_BYTES = 25 * 1024 * 1024;
export const MAX_FILES_PER_TURN = 5;

export function validateSelectedFile(file: File): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!ALLOWED_EXTENSIONS.has(extension)) {
    return "Неподдерживаемый формат файла";
  }
  if (file.size <= 0 || file.size > MAX_INPUT_BYTES) {
    return "Размер файла должен быть от 1 байта до 25 MiB";
  }
  return null;
}

export async function uploadFile(args: {
  file: File;
  workspaceId: string;
  conversationId: string;
}): Promise<FileArtifactRef> {
  const form = new FormData();
  form.set("workspace_id", args.workspaceId);
  form.set("conversation_id", args.conversationId);
  form.set("file", args.file, args.file.name);
  const response = await fetch("/api/files", { method: "POST", body: form });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message.slice(0, 500) || `Ошибка загрузки (${response.status})`);
  }
  return (await response.json()) as FileArtifactRef;
}

export async function deleteStagedFile(artifactId: string): Promise<void> {
  const response = await fetch(`/api/files/${encodeURIComponent(artifactId)}`, {
    method: "DELETE",
  });
  if (!response.ok && response.status !== 404) {
    throw new Error(`Ошибка удаления (${response.status})`);
  }
}

export function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KiB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MiB`;
}
