import type { Metadata } from "next";
import { ThemeSelector } from "@/features/theme/ThemeSelector";
import { OAuthConsentClient } from "./OAuthConsentClient";
import { requireServerSession } from "@/lib/serverSession";

export const metadata: Metadata = {
  title: "Авторизация MCP | Rhetorica",
};

export default async function OAuthConsentPage({
  searchParams,
}: {
  searchParams: Promise<{ transaction?: string }>;
}) {
  const { transaction } = await searchParams;
  const nextPath = transaction
    ? `/oauth/consent?transaction=${encodeURIComponent(transaction)}`
    : "/oauth/consent";
  await requireServerSession(nextPath);

  if (!transaction) {
    return (
      <main className="p-8 text-primary">
        <ThemeSelector />
        <h1 className="mt-4 text-2xl font-semibold">OAuth-запрос не найден</h1>
        <p className="mt-3 text-secondary">Запустите подключение заново из MCP-клиента.</p>
      </main>
    );
  }
  return <>
    <div className="flex justify-end px-5 pt-5"><ThemeSelector /></div>
    <OAuthConsentClient transaction={transaction} />
  </>;
}
