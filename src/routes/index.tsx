import { createFileRoute } from "@tanstack/react-router";
import { CaseDesk } from "@/components/case-desk";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <CaseDesk />;
}
