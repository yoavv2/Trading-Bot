"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { DraftEditor } from "@/components/research/DraftEditor";

export default function DraftPage({ params }: { params: Promise<{ draftId: string }> }) {
  const { draftId } = use(params);
  const router = useRouter();
  return (
    <ResearchGate title="Research · Draft">
      <DraftEditor draftId={draftId} onNavigate={(href) => router.push(href)} />
    </ResearchGate>
  );
}
