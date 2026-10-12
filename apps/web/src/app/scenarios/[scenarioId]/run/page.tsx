import type { Metadata } from "next";
import { requireServerSession } from "@/lib/serverSession";
import { WorkflowLaunch } from "@/features/scenarios/WorkflowLaunch";

export const metadata: Metadata = {
  title: "Запуск сценария | Rhetorica",
};

export default async function RunScenarioPage({ params }: { params: Promise<{ scenarioId: string }> }) {
  const { scenarioId } = await params;
  const session = await requireServerSession(`/scenarios/${scenarioId}/run`);
  return <WorkflowLaunch scenarioId={scenarioId} workspace={session.workspace_id} />;
}
