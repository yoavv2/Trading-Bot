"use client";

import { use } from "react";
import { useRouter } from "next/navigation";
import { ResearchGate } from "@/components/research/ResearchGate";
import { VersionDetail } from "@/components/research/VersionDetail";

export default function VersionPage({ params }: { params: Promise<{ versionId: string }> }) {
  const { versionId } = use(params);
  const router = useRouter();
  return (
    <ResearchGate title="Research · Strategy version">
      <VersionDetail versionId={versionId} onNavigate={(href) => router.push(href)} />
    </ResearchGate>
  );
}
