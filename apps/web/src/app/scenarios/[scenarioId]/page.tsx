import type { Metadata } from "next";
import { requireServerSession } from "@/lib/serverSession";
import { WorkflowEditor } from "@/features/scenarios/WorkflowEditor";

export const metadata: Metadata = {
  title: "Редактор сценария | Rhetorica",
};

export default async function ScenarioPage({ params, searchParams }: { params: Promise<{ scenarioId: string }>; searchParams: Promise<{ scope?: string }> }) {
  const { scenarioId } = await params;
  const query = await searchParams;
  const official = scenarioId === "new" && query.scope === "official";
  const session = await requireServerSession(`/scenarios/${scenarioId}${official ? "?scope=official" : ""}`);
  return <WorkflowEditor scenarioId={scenarioId} workspace={session.workspace_id} initialScope={official && session.role === "admin" ? "official" : "personal"} canCreateOfficial={session.role === "admin"} />;
}
