"use client";

import { useId } from "react";
import {
  PERIOD_SHORTCUTS,
  SPLIT_PRESETS,
  WINDOW_LABELS,
  WINDOW_PURPOSE,
  WINDOW_ROLES,
  computeWindows,
  inclusiveDays,
  issuesFor,
  splitFromWindows,
  splitProblem,
  todayIso,
  type PeriodIssue,
  type Split,
  type WindowRole,
  type Windows,
} from "@/lib/research/windows";

const FIELD = "mt-1 block w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100";
const FIELD_BAD = "mt-1 block w-full rounded border border-red-700 bg-zinc-950 px-2 py-1 text-sm text-zinc-100";

export const WINDOW_COLORS: Record<WindowRole, { bar: string; text: string; border: string }> = {
  development: { bar: "bg-cyan-700", text: "text-cyan-200", border: "border-cyan-800" },
  validation: { bar: "bg-amber-600", text: "text-amber-200", border: "border-amber-800" },
  final_test: { bar: "bg-violet-600", text: "text-violet-200", border: "border-violet-800" },
};

export type PeriodMode = "preset" | "custom";

export type PeriodValue = {
  rangeStart: string;
  rangeEnd: string;
  split: Split;
  mode: PeriodMode;
  windows: Windows;
};

type Props = {
  value: PeriodValue;
  onChange: (next: PeriodValue) => void;
  issues: PeriodIssue[];
  revisionNote: string | null;
};

function Messages({ messages, id }: { messages: string[]; id: string }) {
  if (messages.length === 0) {
    return null;
  }
  return (
    <ul id={id} role="alert" className="mt-1 space-y-0.5 text-xs text-red-300">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

function DateInput({ id, label, value, onChange, messages }: { id: string; label: string; value: string; onChange: (v: string) => void; messages: string[] }) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-zinc-400">
        {label}
      </label>
      <input
        id={id}
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={messages.length > 0 || undefined}
        aria-describedby={messages.length > 0 ? `${id}-msg` : undefined}
        className={messages.length > 0 ? FIELD_BAD : FIELD}
      />
      <Messages id={`${id}-msg`} messages={messages} />
    </div>
  );
}

/** Three-colour bar proportional to each window's inclusive days, with the real dates. */
export function WindowTimeline({ windows }: { windows: Windows }) {
  const days = WINDOW_ROLES.map((role) => Math.max(0, inclusiveDays(windows[role].start, windows[role].end) ?? 0));
  const total = days.reduce((a, b) => a + b, 0);
  return (
    <div data-testid="window-timeline">
      <div className="flex h-3 w-full overflow-hidden rounded" role="img" aria-label={WINDOW_ROLES.map((role, i) => `${WINDOW_LABELS[role]} ${windows[role].start} to ${windows[role].end} (${days[i]} days)`).join("; ")}>
        {WINDOW_ROLES.map((role, index) => (
          <div key={role} className={WINDOW_COLORS[role].bar} style={{ width: total > 0 ? `${(days[index] / total) * 100}%` : "33.3%" }} />
        ))}
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
        {WINDOW_ROLES.map((role, index) => (
          <div key={role} className={`rounded border ${WINDOW_COLORS[role].border} bg-zinc-950/40 px-2 py-1.5`}>
            <dt className={`text-xs font-semibold ${WINDOW_COLORS[role].text}`}>
              {WINDOW_LABELS[role]} <span className="font-normal text-zinc-500">· {days[index]} days</span>
            </dt>
            <dd className="font-mono text-xs text-zinc-200" data-testid={`window-${role}`}>
              {windows[role].start || "—"} → {windows[role].end || "—"}
            </dd>
            <dd className="mt-0.5 text-xs text-zinc-500">{WINDOW_PURPOSE[role]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/**
 * "Research period": a start and end date (with shortcuts), a visible, editable split
 * preset that computes the three windows, a timeline of the resulting dates, and a
 * "Customize periods" mode exposing the six window dates. In preset mode the windows are
 * always recomputed from the range and split; custom mode keeps whatever is entered.
 */
export function ResearchPeriod({ value, onChange, issues, revisionNote }: Props) {
  const id = useId();
  const today = todayIso();
  const computed = computeWindows(value.rangeStart, value.rangeEnd, value.split);
  const problem = splitProblem(value.split);
  const customShare = value.mode === "custom" ? splitFromWindows(value.rangeStart, value.rangeEnd, value.windows) : null;

  function setRange(rangeStart: string, rangeEnd: string) {
    const next = { ...value, rangeStart, rangeEnd };
    if (next.mode === "preset") {
      next.windows = computeWindows(rangeStart, rangeEnd, next.split) ?? emptyWindows();
    }
    onChange(next);
  }

  function setSplit(split: Split) {
    const next = { ...value, split };
    if (next.mode === "preset") {
      next.windows = computeWindows(next.rangeStart, next.rangeEnd, split) ?? emptyWindows();
    }
    onChange(next);
  }

  function setWindow(role: WindowRole, key: "start" | "end", date: string) {
    onChange({ ...value, mode: "custom", windows: { ...value.windows, [role]: { ...value.windows[role], [key]: date } } });
  }

  function customize() {
    onChange({ ...value, mode: "custom", windows: computed ?? value.windows });
  }

  function usePreset() {
    onChange({ ...value, mode: "preset", windows: computeWindows(value.rangeStart, value.rangeEnd, value.split) ?? emptyWindows() });
  }

  const pctInput = (key: keyof Split, label: string) => (
    <label key={key} className="block text-xs text-zinc-400">
      {label} %
      <input
        type="number"
        min={0}
        max={100}
        inputMode="numeric"
        value={Number.isFinite(value.split[key]) ? value.split[key] : ""}
        onChange={(event) => setSplit({ ...value.split, [key]: event.target.value === "" ? Number.NaN : Number(event.target.value) })}
        aria-label={`${label} share percent`}
        className={FIELD}
      />
    </label>
  );

  return (
    <div className="mt-2 space-y-4">
      {revisionNote ? <p className="rounded border border-cyan-900 bg-cyan-950/30 px-3 py-2 text-xs text-cyan-100">{revisionNote}</p> : null}

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <DateInput id={`${id}-start`} label="Start date" value={value.rangeStart} onChange={(v) => setRange(v, value.rangeEnd)} messages={issuesFor(issues, "rangeStart")} />
        <DateInput id={`${id}-end`} label="End date" value={value.rangeEnd} onChange={(v) => setRange(value.rangeStart, v)} messages={issuesFor(issues, "rangeEnd")} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-zinc-500">Shortcuts:</span>
        {PERIOD_SHORTCUTS.map((shortcut) => (
          <button
            key={shortcut.label}
            type="button"
            onClick={() => {
              const range = shortcut.compute(today);
              setRange(range.start, range.end);
            }}
            className="rounded border border-zinc-700 px-2 py-0.5 text-zinc-200 hover:bg-zinc-800"
          >
            {shortcut.label}
          </button>
        ))}
      </div>

      <details className="text-xs text-zinc-500">
        <summary className="cursor-pointer">How dates are read</summary>
        <p className="mt-1">Dates are inclusive calendar dates; the trading sessions inside them are what gets evaluated. Windows must stay inside the period and follow each other without overlapping. Warm-up history before Development is downloaded automatically.</p>
      </details>

      <div className="rounded border border-zinc-800 bg-zinc-950/40 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase text-zinc-400">Split {value.mode === "custom" ? "(custom periods in use)" : "(preset)"}</h3>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            {SPLIT_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => setSplit(preset.split)}
                disabled={value.mode === "custom"}
                className={`rounded border px-2 py-0.5 ${value.mode === "preset" && preset.split.development_pct === value.split.development_pct && preset.split.validation_pct === value.split.validation_pct ? "border-cyan-700 text-cyan-200" : "border-zinc-700 text-zinc-300 hover:bg-zinc-800"} disabled:opacity-40`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>
        <p className="mt-1 text-xs text-zinc-500">A convenience preset, not a methodology: the right shares depend on the strategies, the assets and the history available.</p>
        {value.mode === "preset" ? (
          <div className="mt-2 grid grid-cols-3 gap-2">
            {pctInput("development_pct", "Development")}
            {pctInput("validation_pct", "Validation")}
            {pctInput("final_test_pct", "Final test")}
          </div>
        ) : customShare ? (
          <p className="mt-2 text-xs text-zinc-400">
            Current shares of the period: development {customShare.development_pct}%, validation {customShare.validation_pct}%, final test {customShare.final_test_pct}%.
          </p>
        ) : null}
        {value.mode === "preset" && problem ? (
          <p role="alert" className="mt-1 text-xs text-red-300">
            {problem}
          </p>
        ) : null}
      </div>

      {value.mode === "preset" ? (
        computed ? (
          <WindowTimeline windows={computed} />
        ) : (
          <p className="text-xs text-zinc-500">Enter a start and end date (at least three days apart) to see the windows.</p>
        )
      ) : (
        <WindowTimeline windows={value.windows} />
      )}

      <div className="flex flex-wrap items-center gap-3">
        {value.mode === "preset" ? (
          <button type="button" onClick={customize} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800">
            Customize periods
          </button>
        ) : (
          <button type="button" onClick={usePreset} className="rounded border border-zinc-600 px-3 py-1 text-sm text-zinc-100 hover:bg-zinc-800">
            Use the preset split instead
          </button>
        )}
        <span className="text-xs text-zinc-500">The initial run covers Development and Validation; Final test runs separately after the freeze.</span>
      </div>

      {value.mode === "custom" ? (
        <div className="space-y-3" data-testid="custom-periods">
          {WINDOW_ROLES.map((role) => (
            <fieldset key={role} className={`rounded border ${WINDOW_COLORS[role].border} p-3`}>
              <legend className={`px-1 text-xs font-semibold ${WINDOW_COLORS[role].text}`}>{WINDOW_LABELS[role]}</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <DateInput id={`${id}-${role}-start`} label={`${WINDOW_LABELS[role]} start`} value={value.windows[role].start} onChange={(v) => setWindow(role, "start", v)} messages={issuesFor(issues, `${role}.start`)} />
                <DateInput id={`${id}-${role}-end`} label={`${WINDOW_LABELS[role]} end`} value={value.windows[role].end} onChange={(v) => setWindow(role, "end", v)} messages={issuesFor(issues, `${role}.end`)} />
              </div>
            </fieldset>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function emptyWindows(): Windows {
  return { development: { start: "", end: "" }, validation: { start: "", end: "" }, final_test: { start: "", end: "" } };
}
