import Link from "next/link";
import { ResearchGate } from "@/components/research/ResearchGate";

export default function ResearchHome() {
  return (
    <ResearchGate title="Research">
      <ul className="space-y-2 text-sm">
        <li>
          <Link href="/research/strategies" className="text-cyan-300 hover:underline">Strategies</Link>
          <span className="ml-2 text-zinc-400">author a specification in YAML, validate it, approve an immutable version</span>
        </li>
        <li>
          <Link href="/research/assets" className="text-cyan-300 hover:underline">Assets</Link>
          <span className="ml-2 text-zinc-400">search the catalog and keep saved lists</span>
        </li>
        <li>
          <Link href="/research/studies" className="text-cyan-300 hover:underline">Studies</Link>
          <span className="ml-2 text-zinc-400">define a study, check readiness, run, compare per asset, freeze and run the final test</span>
        </li>
      </ul>
    </ResearchGate>
  );
}
