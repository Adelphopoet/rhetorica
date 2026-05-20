import { redirect } from "next/navigation";
import { LoginPageClient } from "./LoginPageClient";
import { getServerSession } from "@/lib/serverSession";

export default async function LoginPage() {
  const session = await getServerSession();
  if (session) {
    redirect("/");
  }

  return <LoginPageClient />;
}
