import { useState } from "react";
import type { ApiResult } from "./api";

/**
 * Returns the data of the most recent successful `result`, retained while a
 * later refetch fails (WR-C-06). Callers that must keep an open confirmation
 * dialog mounted across a transient read failure use this to keep rendering
 * the same component at the same tree position, hiding only the controls that
 * must not be offered while the state is unknown. Uses the React-documented
 * "adjust state while rendering" pattern rather than a ref write in render.
 */
export function useLastKnownData<T>(result: ApiResult<T> | null): T | null {
  const [lastKnown, setLastKnown] = useState<T | null>(null);
  if (result?.ok && result.data !== lastKnown) {
    setLastKnown(result.data);
  }
  return result?.ok ? result.data : lastKnown;
}
