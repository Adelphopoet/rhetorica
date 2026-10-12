"use client";

import * as React from "react";

import { Alert, Button, Card, CardContent, Input } from "@rhetorica/ui";
import { Cpu, Plus } from "lucide-react";

import type { CodingEnvironmentProfile } from "./types";

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;

function errorMessage(text: string, status: number) {
  try {
    const payload = JSON.parse(text) as { detail?: unknown };
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) return payload.detail.map(item => item.msg).filter(Boolean).join("; ");
    if (payload.detail && typeof payload.detail === "object" && "reason_code" in payload.detail && payload.detail.reason_code === "coding_environment_revision_conflict") return "Профиль уже изменён. Обновите страницу и повторите правку.";
  } catch {}
  return `Запрос завершился с кодом ${status}`;
}

export function EnvironmentProfilesPanel({
  initialProfiles,
}: {
  initialProfiles: CodingEnvironmentProfile[];
}) {
  const [profiles, setProfiles] = React.useState(initialProfiles);
  const [editing, setEditing] = React.useState<CodingEnvironmentProfile | "new" | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const current = editing === "new" ? null : editing;
    const id = current?.id ?? String(data.get("id") ?? "").trim();
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/admin/coding-environments/${encodeURIComponent(id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id,
          expected_revision: current?.revision ?? 0,
          max_runtime_seconds: Number(data.get("runtime_minutes")) * 60,
          cpu_limit: String(data.get("cpu_limit") ?? ""),
          memory_limit: String(data.get("memory_limit") ?? ""),
          disk_limit_bytes: Number(data.get("disk_gib")) * GIB,
          pid_limit: Number(data.get("pid_limit")),
          output_limit_bytes: Number(data.get("output_mib")) * MIB,
          pip_dependencies: String(data.get("pip_dependencies") ?? "").split("\n").map(value => value.trim()).filter(Boolean),
          dependency_sources: String(data.get("dependency_sources") ?? "")
            .split("\n")
            .map((value) => value.trim())
            .filter(Boolean),
        }),
      });
      const text = await response.text();
      if (!response.ok) throw new Error(errorMessage(text, response.status));
      const saved = JSON.parse(text) as CodingEnvironmentProfile;
      setProfiles((items) => [...items.filter((item) => item.id !== saved.id), saved].sort((a, b) => a.id.localeCompare(b.id)));
      setEditing(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось сохранить профиль окружения");
    } finally {
      setSaving(false);
    }
  }

  const current = editing === "new" ? null : editing;
  return (
    <Card className="min-w-0 rounded-lg bg-none shadow-none">
      <CardContent className="space-y-3 p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 font-semibold"><Cpu className="h-4 w-4" />Окружения выполнения кода</div>
            <p className="mt-1 text-sm text-secondary">Версионируемые ресурсы и Python-зависимости. RuntimeClass и образ задаются при развёртывании</p>
          </div>
          <Button size="sm" onClick={() => { setError(null); setEditing("new"); }}><Plus className="h-4 w-4" />Добавить профиль</Button>
        </div>
        {profiles.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {profiles.map((profile) => (
              <button key={profile.id} type="button" onClick={() => { setError(null); setEditing(profile); }} className="rounded-lg border border-control-border p-3 text-left transition hover:border-accent">
                <div className="font-medium">{profile.id} <span className="text-xs text-muted">версия {profile.revision}</span></div>
                <div className="mt-2 text-sm text-secondary">{profile.cpu_limit} CPU · {profile.memory_limit} памяти · {Math.round(profile.disk_limit_bytes / GIB)} GiB диска · {profile.pid_limit} PIDs</div>
                <div className="mt-1 text-xs text-muted">{profile.runtime_class_name} · до {Math.round(profile.max_runtime_seconds / 60)} мин</div>
              </button>
            ))}
          </div>
        ) : <p className="text-sm text-secondary">Профили окружения ещё не настроены</p>}
        {editing ? (
          <form className="grid gap-3 rounded-lg border border-control-border p-4 md:grid-cols-2" onSubmit={save}>
            <label className="text-sm">ID профиля<Input name="id" required pattern="[a-z0-9][a-z0-9_-]*" defaultValue={current?.id ?? ""} disabled={Boolean(current)} /></label>
            <label className="text-sm">Лимит CPU<Input name="cpu_limit" required defaultValue={current?.cpu_limit ?? "1"} placeholder="1000m или 1" /></label>
            <label className="text-sm">Лимит памяти<Input name="memory_limit" required defaultValue={current?.memory_limit ?? "2Gi"} placeholder="2048Mi или 2Gi" /></label>
            <label className="text-sm">Диск, GiB<Input name="disk_gib" required type="number" min="1" defaultValue={current ? Math.round(current.disk_limit_bytes / GIB) : 5} /></label>
            <label className="text-sm">Лимит процессов<Input name="pid_limit" required type="number" min="16" max="4096" defaultValue={current?.pid_limit ?? 128} /></label>
            <label className="text-sm">Объём вывода, MiB<Input name="output_mib" required type="number" min="1" max="1024" defaultValue={current ? Math.round(current.output_limit_bytes / MIB) : 64} /></label>
            <label className="text-sm">Время выполнения, мин<Input name="runtime_minutes" required type="number" min="1" max="1440" defaultValue={current ? Math.round(current.max_runtime_seconds / 60) : 20} /></label>
            <label className="text-sm md:col-span-2">Зависимости Python (pip)<textarea aria-label="Зависимости Python (pip)" name="pip_dependencies" defaultValue={current?.pip_dependencies?.join("\n") ?? ""} className="mt-1 min-h-20 w-full rounded-lg border border-control-border bg-surface px-3 py-2" placeholder={"dbt-core==1.11.15\ndbt-trino==1.10.5\npytest==8.3.5"} /><p className="mt-1 text-xs text-muted">Один пакет с точной версией на строку. Устанавливаются в отдельное окружение developer и QA перед работой. Новая ревизия применяется к новым запускам</p></label>
            <label className="text-sm md:col-span-2">Индексы Python-пакетов (HTTPS)<textarea name="dependency_sources" defaultValue={current?.dependency_sources.join("\n") ?? ""} className="mt-1 min-h-20 w-full rounded-lg border border-control-border bg-surface px-3 py-2" placeholder="https://pypi.org/simple" /><p className="mt-1 text-xs text-muted">Pip использует первый индекс списка. URL без логина, пароля и токена</p></label>
            {error ? <Alert variant="danger" className="md:col-span-2">{error}</Alert> : null}
            <div className="flex justify-end gap-2 md:col-span-2"><Button type="button" variant="ghost" onClick={() => setEditing(null)}>Отмена</Button><Button type="submit" disabled={saving}>{saving ? "Сохранение…" : "Сохранить новую версию"}</Button></div>
          </form>
        ) : null}
      </CardContent>
    </Card>
  );
}
