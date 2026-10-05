import type { ComponentType } from "react";
import type { JobTypeCatalogItem } from "../components/jobs/types";
import type { MutationCapability } from "./useMutationCapability";
import { BacktestJobForm } from "../components/jobs/new/BacktestJobForm";
import { BrokerOrderSyncJobForm } from "../components/jobs/new/BrokerOrderSyncJobForm";
import { IngestBarsJobForm } from "../components/jobs/new/IngestBarsJobForm";
import { ReconciliationJobForm } from "../components/jobs/new/ReconciliationJobForm";
import { RiskEvaluationJobForm } from "../components/jobs/new/RiskEvaluationJobForm";
import { SyncMarketSessionsJobForm } from "../components/jobs/new/SyncMarketSessionsJobForm";
import { SyncSymbolMetadataJobForm } from "../components/jobs/new/SyncSymbolMetadataJobForm";

/**
 * D-17 lookup map (1): job_type -> submission form component. This is the
 * only module permitted to declare JOB_TYPE_FORMS (enforced by
 * consoleBoundaries.test.ts's "Single-lookup-map discipline" scan), and
 * components/jobs/new/NewJobView.tsx is the only file permitted to import
 * it (20.1-14: API-only Job types have no entry -- the catalog marks them api_only) -- every Job list/detail/log/event component (D-17) stays
 * job-type-agnostic and never references this map. Phase 20 adds entries
 * here only.
 */
export type JobSubmissionFormProps = {
  catalogEntry: JobTypeCatalogItem | undefined;
  capability: MutationCapability;
  initialParams: Record<string, string>;
  onNavigate: (href: string) => void;
};

export const JOB_TYPE_FORMS: Readonly<
  Record<string, ComponentType<JobSubmissionFormProps>>
> = {
  backtest: BacktestJobForm,
  "broker-order-sync": BrokerOrderSyncJobForm,
  "ingest-bars": IngestBarsJobForm,
  "reconciliation": ReconciliationJobForm,
  "risk-evaluation": RiskEvaluationJobForm,
  "sync-market-sessions": SyncMarketSessionsJobForm,
  "sync-symbol-metadata": SyncSymbolMetadataJobForm,
};
