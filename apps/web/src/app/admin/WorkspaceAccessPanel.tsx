"use client";

import { adminLabel } from "./adminLabels";

import { useState } from "react";
import { Button, Card, CardContent, CardHeader, CardTitle, Input } from "@rhetorica/ui";
import type {
  WorkspaceAccessItem,
  WorkspaceMembershipItem,
  WorkspaceResourceGrantItem,
  WorkspaceResourceOption,
  WorkspaceUserItem,
} from "./types";

type Props = {
  activeWorkspaceId: string;
  workspaces: WorkspaceAccessItem[];
  initialMemberships: WorkspaceMembershipItem[];
  initialGrants: WorkspaceResourceGrantItem[];
  users: WorkspaceUserItem[];
  resources: WorkspaceResourceOption[];
  canManageCatalog?: boolean;
  canManageMemberships?: boolean;
  canCreateWorkspace?: boolean;
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store" });
  const text = await response.text();
  if (!response.ok) {
    let detail = text || `HTTP ${response.status}`;
    try {
      detail = String((JSON.parse(text) as { detail?: string }).detail ?? detail);
    } catch {
      // Keep the backend text as the actionable error.
    }
    throw new Error(detail);
  }
  return text ? (JSON.parse(text) as T) : (undefined as T);
}

export function WorkspaceAccessPanel({
  activeWorkspaceId,
  workspaces,
  initialMemberships,
  initialGrants,
  users,
  resources,
  canManageCatalog = true,
  canManageMemberships = true,
  canCreateWorkspace = canManageCatalog,
}: Props) {
  const [memberships, setMemberships] = useState(initialMemberships);
  const [grants, setGrants] = useState(initialGrants);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(activeWorkspaceId);
  const defaultWorkspaceId = workspaces.find((item) => item.is_default)?.workspace.id;
  const isDefaultWorkspace = activeWorkspaceId === defaultWorkspaceId;

  async function run(action: () => Promise<void>) {
    setPending(true);
    setError(null);
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Неизвестная ошибка");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="grid gap-3">
      {error ? <p className="rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm text-danger">{error}</p> : null}
      <Card className="min-w-0 rounded-lg bg-none shadow-none">
        <CardHeader className="px-3 py-2.5"><CardTitle className="text-sm">Рабочие пространства</CardTitle></CardHeader>
        <CardContent className="grid min-w-0 gap-3 p-3">
          <div className="grid min-w-0 gap-3">
            <label className="grid min-w-0 gap-1 text-sm">
              <span>Текущее рабочее пространство</span>
              <select
                aria-label="Текущее рабочее пространство"
                className="h-10 min-w-0 w-full rounded-lg border border-control-border bg-surface px-3"
                disabled={pending}
                value={selectedWorkspaceId}
                onChange={(event) => {
                  const workspaceId = event.target.value;
                  setSelectedWorkspaceId(workspaceId);
                  void run(async () => {
                    try {
                      await requestJson(
                        `/api/workspaces/${encodeURIComponent(workspaceId)}/select`,
                        { method: "POST" },
                      );
                    } catch (cause) {
                      setSelectedWorkspaceId(activeWorkspaceId);
                      throw cause;
                    }
                    window.location.reload();
                  });
                }}
              >
                {workspaces.map(({ workspace, membership, is_default }) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name} · {adminLabel(membership.role)}{is_default ? " · основное" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {canCreateWorkspace ? <form
            className="grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              const form = event.currentTarget;
              const data = new FormData(form);
              void run(async () => {
                await requestJson("/api/admin/workspaces", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ id: data.get("id"), name: data.get("name"), metadata: {} }),
                });
                window.location.reload();
              });
            }}
          >
            <Input name="id" label="ID рабочего пространства" placeholder="engineering" required />
            <Input name="name" label="Название" placeholder="Разработка" required />
            <Button type="submit" disabled={pending}>Создать пространство</Button>
          </form> : null}
        </CardContent>
      </Card>

      <Card className="min-w-0 rounded-lg bg-none shadow-none">
        <CardHeader className="px-3 py-2.5"><CardTitle className="text-sm">Участники</CardTitle></CardHeader>
        <CardContent className="grid min-w-0 gap-3 p-3">
          <div className="grid gap-2">
            {memberships.map((membership) => {
              const user = users.find((item) => item.id === membership.user_id);
              const login = membership.user_login || user?.login || "unknown";
              const displayName = membership.user_display_name || user?.display_name;
              return (
                <div key={membership.user_id} className="flex flex-wrap items-center justify-between gap-2 border-b border-border py-2 text-sm last:border-b-0">
                  <span>
                    <span className="font-medium">{displayName || "Имя не задано"}</span>
                    <span className="ml-2 text-secondary">@{login}</span>
                    <span className="ml-2">· {adminLabel(membership.role)}{membership.is_active ? "" : " · доступ отозван"}</span>
                  </span>
                  {canManageMemberships && membership.is_active && !isDefaultWorkspace ? (
                    <Button type="button" size="sm" variant="outline" disabled={pending} onClick={() => void run(async () => {
                      await requestJson(`/api/admin/workspaces/${activeWorkspaceId}/memberships/${membership.user_id}`, { method: "DELETE" });
                      setMemberships((current) => current.map((item) => item.user_id === membership.user_id ? { ...item, is_active: false } : item));
                    })}>Отозвать</Button>
                  ) : null}
                </div>
              );
            })}
          </div>
          {canManageMemberships ? <form
            className="grid gap-3 md:grid-cols-[1fr_180px_auto] md:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const userId = String(data.get("user_id") ?? "");
              const role = String(data.get("role") ?? "analyst");
              void run(async () => {
                const saved = await requestJson<WorkspaceMembershipItem>(
                  `/api/admin/workspaces/${activeWorkspaceId}/memberships/${userId}`,
                  { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: userId, role, is_active: true }) },
                );
                setMemberships((current) => [...current.filter((item) => item.user_id !== saved.user_id), saved]);
              });
            }}
          >
            <label className="grid min-w-0 gap-1 text-sm"><span>Пользователь</span><select name="user_id" required className="h-10 min-w-0 w-full rounded-lg border border-control-border bg-surface px-3">
              {users.filter((user) => user.is_active).map((user) => <option key={user.id} value={user.id}>{user.display_name || "Имя не задано"} · @{user.login}</option>)}
            </select></label>
            <label className="grid min-w-0 gap-1 text-sm"><span>Роль в пространстве</span><select name="role" defaultValue="analyst" className="h-10 min-w-0 w-full rounded-lg border border-control-border bg-surface px-3">
              <option value="owner">Владелец</option><option value="analyst">Аналитик</option>{isDefaultWorkspace ? null : <option value="viewer">Наблюдатель</option>}
            </select></label>
            <Button type="submit" disabled={pending || users.length === 0}>Сохранить участника</Button>
          </form> : null}
        </CardContent>
      </Card>

      {canManageCatalog ? <Card className="min-w-0 rounded-lg bg-none shadow-none">
        <CardHeader className="px-3 py-2.5"><CardTitle className="text-sm">Общие ресурсы</CardTitle></CardHeader>
        <CardContent className="grid min-w-0 gap-3 p-3">
          <div className="grid gap-2">
            {grants.map((grant) => (
              <div key={grant.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-border py-2 text-sm last:border-b-0">
                <span className="min-w-0 [overflow-wrap:anywhere]">{grant.alias} · {grant.resource_kind} · из {grant.owner_workspace_id}{grant.is_active ? "" : " · доступ отозван"}</span>
                {grant.is_active ? <Button type="button" size="sm" variant="outline" disabled={pending} onClick={() => void run(async () => {
                  await requestJson(`/api/admin/workspaces/${activeWorkspaceId}/resource-grants/${grant.id}`, { method: "DELETE" });
                  setGrants((current) => current.map((item) => item.id === grant.id ? { ...item, is_active: false } : item));
                })}>Отозвать</Button> : null}
              </div>
            ))}
          </div>
          <form
            className="grid gap-3 lg:grid-cols-[1fr_1fr_1fr_auto] lg:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              const resourceKey = String(data.get("resource") ?? "");
              const resource = resources.find((item) => `${item.kind}:${item.id}` === resourceKey);
              const recipient = String(data.get("recipient_workspace_id") ?? "");
              if (!resource) return setError("resource_required");
              void run(async () => {
                const saved = await requestJson<WorkspaceResourceGrantItem>(`/api/admin/workspaces/${recipient}/resource-grants`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ resource_kind: resource.kind, resource_id: resource.id, owner_workspace_id: activeWorkspaceId, recipient_workspace_id: recipient, alias: String(data.get("alias") ?? resource.name) }),
                });
                if (recipient === activeWorkspaceId) setGrants((current) => [...current.filter((item) => item.id !== saved.id), saved]);
              });
            }}
          >
            <label className="grid min-w-0 gap-1 text-sm"><span>Ресурс пространства</span><select name="resource" required className="h-10 min-w-0 w-full rounded-lg border border-control-border bg-surface px-3">
              {resources.map((resource) => <option key={`${resource.kind}:${resource.id}`} value={`${resource.kind}:${resource.id}`}>{resource.name} · {resource.kind}</option>)}
            </select></label>
            <label className="grid min-w-0 gap-1 text-sm"><span>Получатель</span><select name="recipient_workspace_id" required className="h-10 min-w-0 w-full rounded-lg border border-control-border bg-surface px-3">
              {workspaces.filter(({ workspace }) => workspace.id !== activeWorkspaceId).map(({ workspace }) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
            </select></label>
            <Input name="alias" label="Псевдоним" placeholder="lake" required />
            <Button type="submit" disabled={pending || resources.length === 0 || workspaces.length < 2}>Поделиться ресурсом</Button>
          </form>
        </CardContent>
      </Card> : null}
    </div>
  );
}
