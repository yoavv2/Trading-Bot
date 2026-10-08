"use client";

import { useRouter } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { StrategiesView } from "@/components/research/StrategiesView";

export default function ResearchStrategiesPage() {
  const router = useRouter();
  return (
    <ResearchGate title="Research · Strategies">
      <StrategiesView onNavigate={(href) => router.push(href)} />
    </ResearchGate>
  );
}
