"use client";

import { useRouter } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { DraftEditor } from "@/components/research/DraftEditor";

export default function NewStrategyPage() {
  const router = useRouter();
  return (
    <ResearchGate title="Research · New strategy">
      <DraftEditor draftId={null} onNavigate={(href) => router.push(href)} />
    </ResearchGate>
  );
}
