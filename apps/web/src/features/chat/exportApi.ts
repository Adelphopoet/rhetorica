export type AnalysisExportFormat = "html" | "pdf" | "ipynb";

function buildExportUrl(
  conversationId: string,
  messageId: string,
  format: AnalysisExportFormat
): string {
  return `/api/chat/conversations/${conversationId}/messages/${messageId}/exports/${format}`;
}

function resolveFilename(
  headerValue: string | null,
  format: AnalysisExportFormat
): string {
  const match = headerValue?.match(/filename="([^"]+)"/i);
  return match?.[1] ?? `analysis-export.${format}`;
}

export async function downloadAnalysisExport(args: {
  conversationId: string;
  messageId: string;
  format: AnalysisExportFormat;
}): Promise<void> {
  const response = await fetch(
    buildExportUrl(args.conversationId, args.messageId, args.format),
    {
      cache: "no-store",
    }
  );

  if (!response.ok) {
    let detail = "";
    try {
      const payload = (await response.json()) as { body?: string; message?: string; error?: string };
      detail = payload.message ?? payload.body ?? payload.error ?? "";
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Export failed with status ${response.status}`);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = resolveFilename(response.headers.get("Content-Disposition"), args.format);
  link.click();
  URL.revokeObjectURL(url);
}
