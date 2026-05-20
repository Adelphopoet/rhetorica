import { redirect } from "next/navigation";
import { fetchBackend } from "@/lib/backendAuth";

export type AuthSession = {
  workspace_id: string;
  role: string;
  expires_at: string;
  user: {
    id: string;
    login: string;
    display_name: string | null;
    auth_provider: string;
    auth_subject: string;
    is_active: boolean;
    is_bootstrap_user: boolean;
  };
};

export async function getServerSession(): Promise<AuthSession | null> {
  try {
    const response = await fetchBackend("/v1/auth/session");
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as AuthSession;
  } catch {
    return null;
  }
}

export async function requireServerSession(nextPath: string): Promise<AuthSession> {
  const session = await getServerSession();
  if (!session) {
    redirect(`/login?next=${encodeURIComponent(nextPath)}`);
  }
  return session;
}

export async function requireAdminSession(nextPath: string): Promise<AuthSession> {
  const session = await requireServerSession(nextPath);
  if (session.role !== "admin") {
    redirect("/");
  }
  return session;
}
