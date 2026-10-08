"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { StudyWizard } from "@/components/research/StudyWizard";

/** Reads `from` (a study id: the wizard then creates a NEW REVISION of that study). */
function NewStudyRoute() {
  const router = useRouter();
  const search = useSearchParams();
  const fromStudyId = search.get("from");
  return <StudyWizard key={fromStudyId ?? "new"} fromStudyId={fromStudyId} onNavigate={(href) => router.push(href)} />;
}

export default function NewStudyPage() {
  return (
    <ResearchGate title="Research · New study">
      <Suspense fallback={null}>
        <NewStudyRoute />
      </Suspense>
    </ResearchGate>
  );
}
