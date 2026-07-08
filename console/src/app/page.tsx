import { HealthPanel } from "@/components/status/HealthPanel";
import { ReadinessPanel } from "@/components/status/ReadinessPanel";
import { SystemInfoPanel } from "@/components/status/SystemInfoPanel";
import { KillSwitchPanel } from "@/components/status/KillSwitchPanel";
import { LatestRunPanel } from "@/components/status/LatestRunPanel";

/**
 * System status screen (STAT-01/STAT-02/STAT-03): composes the five status panels.
 * Each panel wraps its content in the shared `StatusPanel` chrome (title, FetchMeta
 * as-of/refresh header, endpoint-named error state) so every panel degrades honestly
 * on its own when its endpoint misbehaves (CONS-02/CONS-03).
 */
export default function Home() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-4 text-xl font-semibold text-zinc-100">
        System Status
      </h1>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <HealthPanel />
        <ReadinessPanel />
        <SystemInfoPanel />
        <KillSwitchPanel />
        <LatestRunPanel />
      </div>
    </main>
  );
}
