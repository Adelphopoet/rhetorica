"use client";

import { useRef, useState } from "react";
import { Button } from "@rhetorica/ui";
import { FileUp, LoaderCircle, Paperclip, X } from "lucide-react";
import { formatFileSize, MAX_FILES_PER_TURN, type FileArtifactRef } from "@/features/files/fileApi";

const acceptedFiles = ".txt,.md,.csv,.tsv,.json,.parquet,.xlsx,.docx,.pptx,.pdf,.png,.jpg,.jpeg";

export function scenarioFileError(message: string) {
  if (message === "Unsupported file format") return "Этот формат файла не поддерживается";
  if (message === "File must be between 1 byte and 25 MiB") return "Размер файла должен быть от 1 байта до 25 MiB";
  return message;
}

export function artifactFiles(value: unknown): FileArtifactRef[] {
  const candidates = Array.isArray(value) ? value : value ? [value] : [];
  return candidates.filter((item): item is FileArtifactRef => Boolean(
    item && typeof item === "object" && typeof (item as FileArtifactRef).id === "string" && typeof (item as FileArtifactRef).filename === "string",
  ));
}

export function ScenarioFileParameter({
  id,
  label,
  multiple,
  required,
  value,
  disabled,
  uploading,
  onSelect,
  onRemove,
}: {
  id: string;
  label: string;
  multiple: boolean;
  required: boolean;
  value: unknown;
  disabled: boolean;
  uploading: boolean;
  onSelect: (files: File[]) => void;
  onRemove: (file: FileArtifactRef) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const files = artifactFiles(value);
  const remaining = multiple ? Math.max(0, MAX_FILES_PER_TURN - files.length) : files.length ? 0 : 1;
  const accept = (items: FileList | File[]) => {
    const selected = Array.from(items).slice(0, remaining);
    if (selected.length) onSelect(selected);
    if (input.current) input.current.value = "";
  };

  return <fieldset className="space-y-3" aria-busy={uploading}>
    <legend className="text-sm">{label} {required && <span className="text-cyan">*</span>}<span className="ml-2 text-xs text-muted">{multiple ? "Файлы" : "Файл"}</span></legend>
    <input
      ref={input}
      id={id}
      type="file"
      className="sr-only"
      accept={acceptedFiles}
      multiple={multiple}
      disabled={disabled || remaining === 0}
      aria-label={`Загрузить ${label}`}
      onChange={event => event.target.files && accept(event.target.files)}
    />
    <div
      role="button"
      tabIndex={disabled || remaining === 0 ? -1 : 0}
      aria-disabled={disabled || remaining === 0}
      aria-label={`Область загрузки: ${label}`}
      onClick={() => !disabled && remaining > 0 && input.current?.click()}
      onKeyDown={event => {
        if ((event.key === "Enter" || event.key === " ") && !disabled && remaining > 0) {
          event.preventDefault(); input.current?.click();
        }
      }}
      onDragEnter={event => { event.preventDefault(); if (!disabled && remaining > 0) setDragging(true); }}
      onDragOver={event => event.preventDefault()}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
      onDrop={event => { event.preventDefault(); setDragging(false); if (!disabled && remaining > 0) accept(event.dataTransfer.files); }}
      className={`flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-5 py-4 text-center transition ${dragging ? "border-cyan bg-cyan-subtle/30" : "border-border-strong bg-surface/30 hover:border-cyan-border"} ${(disabled || remaining === 0) ? "cursor-default opacity-60" : ""}`}
    >
      {uploading ? <LoaderCircle className="mb-2 h-6 w-6 animate-spin text-cyan" /> : <FileUp className="mb-2 h-6 w-6 text-cyan" />}
      <span className="text-sm text-primary">{uploading ? "Загружаю…" : "Перетащите сюда или выберите файл"}</span>
      <span className="mt-1 text-xs text-muted">До 25 MiB · CSV, XLSX, JSON, Parquet, документы и изображения{multiple ? ` · до ${MAX_FILES_PER_TURN} файлов` : ""}</span>
    </div>
    {files.length > 0 && <div className="space-y-2" aria-label={`Загруженные файлы: ${label}`}>
      {files.map(file => <div key={file.id} className="flex items-center gap-3 rounded-xl border border-border-strong bg-elevated/70 px-3 py-2 text-sm">
        <Paperclip className="h-4 w-4 shrink-0 text-cyan" />
        <span className="min-w-0 flex-1 truncate" title={file.filename}>{file.filename}</span>
        <span className="shrink-0 text-xs text-muted">{formatFileSize(file.size_bytes)}</span>
        <Button type="button" size="icon" variant="ghost" disabled={disabled} aria-label={`Удалить ${file.filename}`} onClick={event => { event.stopPropagation(); onRemove(file); }}><X className="h-4 w-4" /></Button>
      </div>)}
    </div>}
    {required && !files.length && !uploading && <p className="text-xs text-warning">Загрузите файл перед запуском.</p>}
  </fieldset>;
}
