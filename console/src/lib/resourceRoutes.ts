// Lookup map (2) of the console's exactly-two lookup maps (D-17): Job
// resources[].kind -> console route. Keyed by resource kind, never by Job
// type — this is what keeps the generic resource-link renderer job-type-
// agnostic. Phase 19 defines exactly one linked-resource kind (a strategy
// run); an unrecognized kind resolves to null so the renderer falls back
// to plain `{kind}: {id}` text instead of a broken link.

export const RESOURCE_ROUTES: Readonly<Record<string, (id: string) => string>> = {
  strategy_run: (id: string) => `/runs/${encodeURIComponent(id)}`,
};

export function resourceHref(kind: string, id: string): string | null {
  const build = RESOURCE_ROUTES[kind];
  return build ? build(id) : null;
}
