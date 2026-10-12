import { Skeleton, Spinner } from "@rhetorica/ui";

export function RouteLoadingShell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top_right,rgb(var(--color-accent)/0.18),transparent_22%),radial-gradient(circle_at_top_left,rgb(var(--color-elevated)/0.9),transparent_30%),linear-gradient(180deg,rgb(var(--color-page)),rgb(var(--color-surface))_48%,rgb(var(--color-page)))] px-4 py-5 md:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-6">
        <p role="status" className="flex items-center gap-2 text-sm text-secondary">
          <Spinner size="md" />
          {label}
        </p>
        {children}
      </div>
    </main>
  );
}

export function AdminRouteLoading() {
  return (
    <main className="min-h-screen bg-page px-4 py-4 md:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4">
        <p role="status" className="flex items-center gap-2 text-sm text-secondary">
          <Spinner size="md" />
          Загружаю консоль администратора…
        </p>
        <header className="border-b border-border pb-4">
          <Skeleton className="h-7 w-full max-w-72" />
          <Skeleton className="mt-2 h-4 w-full max-w-lg" />
        </header>
        <div className="flex flex-wrap gap-4 border-b border-border pb-2">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-8 w-20 rounded-md" />
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28 w-full rounded-lg" />
          ))}
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          <Skeleton className="h-40 w-full rounded-lg" />
          <Skeleton className="h-80 w-full rounded-lg" />
        </div>
      </div>
    </main>
  );
}

export function ScenarioRouteLoading() {
  return (
    <RouteLoadingShell label="Загружаю сценарий…">
      <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-surface px-5 py-3">
        <Skeleton className="h-9 w-64" />
        <div className="flex gap-2">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-8 w-24 rounded-lg" />
          ))}
        </div>
      </header>
      <div className="grid gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]">
        <Skeleton className="h-[560px] w-full rounded-2xl" />
        <Skeleton className="h-[560px] w-full rounded-2xl" />
      </div>
    </RouteLoadingShell>
  );
}
