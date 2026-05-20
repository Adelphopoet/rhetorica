import { Skeleton } from "@rhetorica/ui";

export default function Loading() {
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_right,rgba(93,168,255,0.18),transparent_22%),radial-gradient(circle_at_top_left,rgba(18,26,42,0.9),transparent_30%),linear-gradient(180deg,#04070d,#09111d_48%,#060910)] px-3 py-3 sm:px-4 sm:py-4">
      <div className="mx-auto grid min-h-[calc(100vh-24px)] max-w-[1800px] overflow-hidden rounded-[32px] border border-[#142033] bg-[linear-gradient(180deg,rgba(8,12,21,0.94),rgba(7,11,19,0.98))] shadow-[0_24px_80px_rgba(0,0,0,0.4)] lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_minmax(420px,540px)]">
        <aside className="hidden border-r border-[#142033] p-4 lg:flex lg:flex-col">
          <Skeleton className="h-28 w-full rounded-[24px]" />
          <Skeleton className="mt-6 h-12 w-full rounded-2xl" />
          <Skeleton className="mt-3 h-12 w-full rounded-2xl" />
          <Skeleton className="mt-3 h-12 w-full rounded-2xl" />
        </aside>

        <section className="flex min-w-0 flex-col">
          <div className="border-b border-[#172338] px-4 py-4 sm:px-5 xl:px-6">
            <Skeleton className="h-10 w-full max-w-[820px]" />
          </div>
          <div className="flex-1 space-y-4 p-4 sm:p-5 xl:p-6">
            <Skeleton className="h-44 w-full rounded-[28px]" />
            <Skeleton className="h-36 w-[78%] rounded-[28px]" />
            <Skeleton className="h-44 w-[86%] rounded-[28px]" />
          </div>
          <div className="border-t border-[#19253a] px-4 pb-4 pt-3 sm:px-5 xl:px-6">
            <Skeleton className="h-40 w-full rounded-[28px]" />
          </div>
        </section>

        <aside className="hidden border-l border-[#172338] p-6 xl:flex xl:flex-col">
          <Skeleton className="h-5 w-44" />
          <Skeleton className="mt-6 h-32 w-full rounded-[24px]" />
          <Skeleton className="mt-5 h-80 w-full rounded-[24px]" />
        </aside>
      </div>
    </main>
  );
}
