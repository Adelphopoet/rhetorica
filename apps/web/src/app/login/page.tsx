import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginPageClient } from "./LoginPageClient";
import { getServerSession } from "@/lib/serverSession";
import { safeNextPath } from "@/lib/safeNextPath";

export const metadata: Metadata = {
  title: "Rhetorica",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const params = await searchParams;
  const nextPath = safeNextPath(params.next);
  const session = await getServerSession();
  if (session) {
    redirect(nextPath);
  }

  return <LoginPageClient />;
}
