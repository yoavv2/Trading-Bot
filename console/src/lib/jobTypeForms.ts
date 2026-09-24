import type { ComponentType } from "react";
import type { JobTypeCatalogItem } from "../components/jobs/types";
import type { MutationCapability } from "./useMutationCapability";
import { BacktestJobForm } from "../components/jobs/new/BacktestJobForm";

/**
 * D-17 lookup map (1): job_type -> submission form component. This is the
 * only module permitted to declare JOB_TYPE_FORMS (enforced by
 * consoleBoundaries.test.ts's "Single-lookup-map discipline" scan), and
 * components/jobs/new/NewJobView.tsx is the only file permitted to import
 * it -- every Job list/detail/log/event component (D-17) stays
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
};
