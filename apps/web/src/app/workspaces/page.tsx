import { WorkspaceCodingDefaultsPanel } from "@/features/coding/WorkspaceCodingDefaultsPanel";
import type { Metadata } from "next";
import Link from "next/link";
import { X } from "lucide-react";

import { WorkspaceAccessPanel } from "@/app/admin/WorkspaceAccessPanel";
import type {
  WorkspaceAccessItem,
  WorkspaceMembershipItem,
  WorkspaceUserItem,
} from "@/app/admin/types";
import { fetchBackend } from "@/lib/backendAuth";
import { requireServerSession } from "@/lib/serverSession";

async function fetchItems<T>(path: string): Promise<T[]> {
  const response = await fetchBackend(path, { cache: "no-store" });
  return response.ok ? ((await response.json()) as T[]) : [];
}

export const metadata: Metadata = { title: "Workspaces | Rhetorica" };

export default async function WorkspacesPage() {
  const session = await requireServerSession("/workspaces");
  const canManageMemberships = session.role === "admin" || session.workspace_role === "owner";
  const [workspaces, memberships, users] = await Promise.all([
    fetchItems<WorkspaceAccessItem>("/v1/workspaces"),
    canManageMemberships
      ? fetchItems<WorkspaceMembershipItem>(
          `/v1/admin/workspaces/${encodeURIComponent(session.workspace_id)}/memberships`,
        )
      : Promise.resolve([]),
    canManageMemberships
      ? fetchItems<WorkspaceUserItem>("/v1/workspaces/user-directory")
      : Promise.resolve([]),
  ]);

  return (
    <main className="min-h-screen bg-page p-6 text-primary">
      <div className="mx-auto grid max-w-5xl gap-5">
        <header className="relative flex flex-wrap items-start justify-between gap-4 pr-12">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">Access</p>
            <h1 className="mt-2 text-3xl font-semibold">Workspaces</h1>
            <p className="mt-2 text-sm text-secondary">
              Choose the active workspace. Owners can manage its memberships.
            </p>
            {canManageMemberships ? (
              <Link
                href="/admin"
                className="mt-3 inline-flex text-sm font-medium text-accent hover:text-primary"
              >
                Manage workspace resources
              </Link>
            ) : null}
          </div>
          <Link
            href="/"
            aria-label="Перейти в рабочее пространство"
            title="Перейти в рабочее пространство"
            className="absolute right-0 top-0 z-10 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-accent/50 bg-elevated text-primary shadow-[0_10px_28px_rgb(var(--color-accent)/0.14)] transition hover:bg-border-strong"
          >
            <X className="h-4 w-4" />
          </Link>
        </header>
        <WorkspaceCodingDefaultsPanel workspaceId={session.workspace_id} canManage={canManageMemberships} />
        <WorkspaceAccessPanel
          activeWorkspaceId={session.workspace_id}
          workspaces={workspaces}
          initialMemberships={memberships}
          initialGrants={[]}
          users={users}
          resources={[]}
          canManageCatalog={false}
          canManageMemberships={canManageMemberships}
          canCreateWorkspace={session.role === "admin"}
        />
      </div>
    </main>
  );
}
