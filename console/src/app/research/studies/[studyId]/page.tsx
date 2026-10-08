"use client";

import { use } from "react";
import { useSearchParams } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { StudyDetail } from "@/components/research/StudyDetail";

export default function StudyPage({ params }: { params: Promise<{ studyId: string }> }) {
  const { studyId } = use(params);
  const search = useSearchParams();
  return (
    <ResearchGate title="Research · Study">
      <StudyDetail studyId={studyId} initialRevisionId={search.get("revision")} />
    </ResearchGate>
  );
}
