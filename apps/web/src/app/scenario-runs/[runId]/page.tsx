import type { Metadata } from "next";
import { requireServerSession } from "@/lib/serverSession";
import { WorkflowRunView } from "@/features/scenarios/WorkflowRunView";

export const metadata: Metadata = {
  title: "Детали запуска | Rhetorica",
};

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const session = await requireServerSession(`/scenario-runs/${runId}`);
  return <main className="min-h-screen bg-[radial-gradient(ellipse_at_top_left,rgb(var(--color-cyan)/0.08),transparent_55%)] bg-surface px-4 py-6 text-primary sm:px-8 sm:py-8"><div className="mx-auto max-w-6xl"><WorkflowRunView runId={runId} workspace={session.workspace_id} /></div></main>;
}
