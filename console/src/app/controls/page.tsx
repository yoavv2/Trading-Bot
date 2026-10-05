"use client";

import { KillSwitchControlTrigger } from "@/components/controls/KillSwitchControlTrigger";
import { ActivePaperStrategyLine } from "@/components/controls/ActivePaperStrategyLine";
import { StrategyControlSection } from "@/components/controls/StrategyControlSection";
import { KillSwitchPanel } from "@/components/status/KillSwitchPanel";

/**
 * D-13: the operator's dedicated control surface (CTRL-01/CTRL-02). A client
 * page because KillSwitchPanel's `renderAction` is a function prop, which cannot
 * cross the server-to-client component boundary.
 */
export default function ControlsPage() {
  return (
    <main className="flex-1 p-6">
      <h1 className="mb-2 text-xl font-semibold text-zinc-100">Controls</h1>
      <div className="mb-4">
        <ActivePaperStrategyLine />
      </div>
      <div className="space-y-6">
        <KillSwitchPanel
          renderAction={(data, meta) => (
            <KillSwitchControlTrigger
              isTripped={data.is_tripped}
              stateKnown={meta.stateKnown}
            />
          )}
        />
        <StrategyControlSection />
      </div>
    </main>
  );
}
