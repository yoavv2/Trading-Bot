// Research-period math for the study wizard. Pure functions, mirrored on the backend's
// readiness rules (services/research/studies.py `_readiness`): dates are INCLUSIVE calendar
// dates, every window has start <= end, every window lies inside the range, and the three
// windows are strictly ordered with no overlap (development.end < validation.start,
// validation.end < final_test.start). The backend derives trading sessions from these
// calendar dates (warm-up history before development is downloaded automatically); there
// is no backend minimum window length, so the only hard floor here is one day per window.

export type Window = { start: string; end: string };
export type Windows = { development: Window; validation: Window; final_test: Window };
export type Split = { development_pct: number; validation_pct: number; final_test_pct: number };
export type WindowRole = keyof Windows;

export const WINDOW_ROLES: WindowRole[] = ["development", "validation", "final_test"];

export const WINDOW_LABELS: Record<WindowRole, string> = {
  development: "Development",
  validation: "Validation",
  final_test: "Final test",
};

export const WINDOW_PURPOSE: Record<WindowRole, string> = {
  development: "Develop and refine the strategy.",
  validation: "Evaluate it on a separate period.",
  final_test: "Final evaluation after decisions are frozen; a separate action, not part of the initial run.",
};

/** The convenience preset (not a research methodology): 60 / 20 / 20. */
export const DEFAULT_SPLIT: Split = { development_pct: 60, validation_pct: 20, final_test_pct: 20 };

export const SPLIT_PRESETS: { label: string; split: Split }[] = [
  { label: "60 / 20 / 20", split: { development_pct: 60, validation_pct: 20, final_test_pct: 20 } },
  { label: "70 / 15 / 15", split: { development_pct: 70, validation_pct: 15, final_test_pct: 15 } },
  { label: "50 / 25 / 25", split: { development_pct: 50, validation_pct: 25, final_test_pct: 25 } },
];

const DAY_MS = 86_400_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Parse an ISO calendar date as a UTC day number; null for anything else. */
export function dayNumber(value: string): number | null {
  if (!ISO_DATE.test(value)) {
    return null;
  }
  const ms = Date.parse(`${value}T00:00:00Z`);
  if (Number.isNaN(ms)) {
    return null;
  }
  // Reject 2024-02-30 style inputs that Date.parse rolls over.
  if (new Date(ms).toISOString().slice(0, 10) !== value) {
    return null;
  }
  return Math.round(ms / DAY_MS);
}

export function isoFromDay(day: number): string {
  return new Date(day * DAY_MS).toISOString().slice(0, 10);
}

export function addDays(value: string, days: number): string {
  const day = dayNumber(value);
  return day === null ? value : isoFromDay(day + days);
}

/** Inclusive day count of a window, or null when either date is unparseable. */
export function inclusiveDays(start: string, end: string): number | null {
  const a = dayNumber(start);
  const b = dayNumber(end);
  if (a === null || b === null) {
    return null;
  }
  return b - a + 1;
}

export function isSplitComplete(split: Split): boolean {
  const values = [split.development_pct, split.validation_pct, split.final_test_pct];
  return values.every((v) => Number.isInteger(v) && v >= 0 && v <= 100) && values.reduce((a, b) => a + b, 0) === 100;
}

/** Why a split cannot be used, or null when it can. */
export function splitProblem(split: Split): string | null {
  const values = [split.development_pct, split.validation_pct, split.final_test_pct];
  if (values.some((v) => !Number.isInteger(v) || v < 0 || v > 100)) {
    return "Each share must be a whole number between 0 and 100.";
  }
  const sum = values.reduce((a, b) => a + b, 0);
  if (sum !== 100) {
    return `The three shares add up to ${sum}%, not 100%.`;
  }
  if (values.some((v) => v === 0)) {
    return "Every window needs a share above 0%.";
  }
  return null;
}

/**
 * Split an inclusive date range into three contiguous, non-overlapping inclusive windows.
 * Development gets round(total × dev%), validation round(total × val%), the final test
 * the remainder, each at least one day. Returns null when the range is unusable (bad dates,
 * end before start, fewer than three days) or the split does not add up to 100.
 */
export function computeWindows(rangeStart: string, rangeEnd: string, split: Split): Windows | null {
  const first = dayNumber(rangeStart);
  const last = dayNumber(rangeEnd);
  if (first === null || last === null || last < first || splitProblem(split) !== null) {
    return null;
  }
  const total = last - first + 1;
  if (total < 3) {
    return null;
  }
  let dev = Math.max(1, Math.round((total * split.development_pct) / 100));
  let val = Math.max(1, Math.round((total * split.validation_pct) / 100));
  // Keep at least one day for the final test.
  while (dev + val > total - 1) {
    if (dev >= val && dev > 1) {
      dev -= 1;
    } else if (val > 1) {
      val -= 1;
    } else {
      break;
    }
  }
  const devEnd = first + dev - 1;
  const valEnd = devEnd + val;
  return {
    development: { start: isoFromDay(first), end: isoFromDay(devEnd) },
    validation: { start: isoFromDay(devEnd + 1), end: isoFromDay(valEnd) },
    final_test: { start: isoFromDay(valEnd + 1), end: isoFromDay(last) },
  };
}

/** The share of the range each window covers (rounded, for display of custom windows). */
export function splitFromWindows(rangeStart: string, rangeEnd: string, windows: Windows): Split | null {
  const total = inclusiveDays(rangeStart, rangeEnd);
  if (total === null || total <= 0) {
    return null;
  }
  const pct = (w: Window) => {
    const days = inclusiveDays(w.start, w.end);
    return days === null ? 0 : Math.round((days / total) * 100);
  };
  return { development_pct: pct(windows.development), validation_pct: pct(windows.validation), final_test_pct: pct(windows.final_test) };
}

export function windowsEqual(a: Windows, b: Windows): boolean {
  return WINDOW_ROLES.every((role) => a[role].start === b[role].start && a[role].end === b[role].end);
}

export type PeriodField = "rangeStart" | "rangeEnd" | `${WindowRole}.start` | `${WindowRole}.end` | "windows";

export type PeriodIssue = { field: PeriodField; message: string };

/**
 * Validation mirroring the backend (pydantic date parsing + readiness window rules), keyed
 * by the control the message belongs beside. An empty result means the backend will accept
 * the dates; readiness may still report data coverage or market-session problems.
 */
export function validatePeriod(rangeStart: string, rangeEnd: string, windows: Windows): PeriodIssue[] {
  const issues: PeriodIssue[] = [];
  const rs = dayNumber(rangeStart);
  const re = dayNumber(rangeEnd);
  if (rs === null) {
    issues.push({ field: "rangeStart", message: rangeStart ? "Enter a valid date (YYYY-MM-DD)." : "Start date is required." });
  }
  if (re === null) {
    issues.push({ field: "rangeEnd", message: rangeEnd ? "Enter a valid date (YYYY-MM-DD)." : "End date is required." });
  }
  if (rs !== null && re !== null && re < rs) {
    issues.push({ field: "rangeEnd", message: "End date is before the start date." });
  }
  if (rs !== null && re !== null && re - rs + 1 < 3) {
    issues.push({ field: "rangeEnd", message: "The period needs at least three days: one per window." });
  }
  const days: Record<WindowRole, { start: number | null; end: number | null }> = {
    development: { start: null, end: null },
    validation: { start: null, end: null },
    final_test: { start: null, end: null },
  };
  for (const role of WINDOW_ROLES) {
    const w = windows[role];
    const s = dayNumber(w.start);
    const e = dayNumber(w.end);
    days[role] = { start: s, end: e };
    const label = WINDOW_LABELS[role];
    if (s === null) {
      issues.push({ field: `${role}.start`, message: w.start ? "Enter a valid date (YYYY-MM-DD)." : `${label} start is required.` });
    }
    if (e === null) {
      issues.push({ field: `${role}.end`, message: w.end ? "Enter a valid date (YYYY-MM-DD)." : `${label} end is required.` });
    }
    if (s !== null && e !== null && s > e) {
      issues.push({ field: `${role}.end`, message: `${label} ends before it starts.` });
    }
    if (s !== null && rs !== null && s < rs) {
      issues.push({ field: `${role}.start`, message: `${label} starts before the research period (${rangeStart}).` });
    }
    if (e !== null && re !== null && e > re) {
      issues.push({ field: `${role}.end`, message: `${label} ends after the research period (${rangeEnd}).` });
    }
  }
  const d = days.development;
  const v = days.validation;
  const f = days.final_test;
  if (d.end !== null && v.start !== null && !(d.end < v.start)) {
    issues.push({ field: "validation.start", message: "Validation must start after development ends (no overlap, no shared day)." });
  }
  if (v.end !== null && f.start !== null && !(v.end < f.start)) {
    issues.push({ field: "final_test.start", message: "Final test must start after validation ends (no overlap, no shared day)." });
  }
  return issues;
}

export function issuesFor(issues: PeriodIssue[], field: PeriodField): string[] {
  return issues.filter((issue) => issue.field === field).map((issue) => issue.message);
}

export type PeriodShortcut = { label: string; compute: (today: string) => { start: string; end: string } };

/** Last day of a month (1-based month), leap years included. */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** The same month and day `years` earlier, clamped to that month's length (Feb 29 → Feb 28). */
export function yearsBefore(value: string, years: number): string {
  const [y, m, d] = value.split("-").map(Number);
  const year = y - years;
  const day = Math.min(d, daysInMonth(year, m));
  return `${String(year).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function yearsBack(today: string, years: number): { start: string; end: string } {
  return { start: yearsBefore(today, years), end: today };
}

export const PERIOD_SHORTCUTS: PeriodShortcut[] = [
  { label: "Last 5 years", compute: (today) => yearsBack(today, 5) },
  { label: "Last 10 years", compute: (today) => yearsBack(today, 10) },
  { label: "Last 15 years", compute: (today) => yearsBack(today, 15) },
  {
    label: "Through last year-end",
    compute: (today) => {
      const lastYear = Number(today.slice(0, 4)) - 1;
      return { start: `${lastYear - 9}-01-01`, end: `${lastYear}-12-31` };
    },
  },
];

/** The user's LOCAL calendar date (date pickers are local), never the UTC date. */
export function todayIso(now: Date = new Date()): string {
  return `${String(now.getFullYear()).padStart(4, "0")}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}
