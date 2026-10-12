import { Skeleton, Spinner } from "@rhetorica/ui";

export default function Loading() {
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.18),transparent_22%),radial-gradient(circle_at_top_left,rgb(var(--color-elevated)/0.9),transparent_30%),linear-gradient(180deg,rgb(var(--color-page)),rgb(var(--color-surface))_48%,rgb(var(--color-page)))] px-3 py-3 sm:px-4 sm:py-4">
      <p role="status" className="mx-auto mb-3 flex w-fit items-center gap-2 rounded-full border border-border bg-surface/90 px-4 py-2 text-xs text-secondary">
        <Spinner size="sm" />
        Загружаю рабочее пространство…
      </p>
      <div className="mx-auto grid min-h-[calc(100vh-4rem)] overflow-hidden rounded-[32px] border border-border bg-[linear-gradient(180deg,rgb(var(--color-surface)/0.94),rgb(var(--color-page)/0.98))] shadow-[0_24px_80px_rgb(var(--color-shadow)/calc(0.4*var(--shadow-opacity-scale)))] lg:grid-cols-[264px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,1fr)_minmax(420px,540px)]">
        <aside className="hidden border-r border-border p-4 lg:flex lg:flex-col">
          <Skeleton className="h-28 w-full rounded-[24px]" />
          <Skeleton className="mt-6 h-12 w-full rounded-2xl" />
          <Skeleton className="mt-3 h-12 w-full rounded-2xl" />
          <Skeleton className="mt-3 h-12 w-full rounded-2xl" />
        </aside>

        <section className="flex min-w-0 flex-col">
          <div className="border-b border-border px-4 py-4 sm:px-5 xl:px-6">
            <Skeleton className="h-10 w-full max-w-[820px]" />
          </div>
          <div className="flex-1 space-y-4 p-4 sm:p-5 xl:p-6">
            <Skeleton className="h-44 w-full rounded-[28px]" />
            <Skeleton className="h-36 w-[78%] rounded-[28px]" />
            <Skeleton className="h-44 w-[86%] rounded-[28px]" />
          </div>
          <div className="border-t border-border px-4 pb-4 pt-3 sm:px-5 xl:px-6">
            <Skeleton className="h-40 w-full rounded-[28px]" />
          </div>
        </section>

        <aside className="hidden border-l border-border p-6 xl:flex xl:flex-col">
          <Skeleton className="h-5 w-44" />
          <Skeleton className="mt-6 h-32 w-full rounded-[24px]" />
          <Skeleton className="mt-5 h-80 w-full rounded-[24px]" />
        </aside>
      </div>
    </main>
  );
}
