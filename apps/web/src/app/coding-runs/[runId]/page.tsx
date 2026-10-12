import { requireServerSession } from "@/lib/serverSession";
import { CodingRunView } from "@/features/coding/CodingRunView";
export default async function Page({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  await requireServerSession(`/coding-runs/${runId}`);
  return <main className="min-h-screen bg-surface p-6 text-primary"><CodingRunView runId={runId} /></main>;
}
