"use client";

import { useEffect, useRef, useState } from "react";

/**
 * useState backed by localStorage. The initial render always uses ``initial``
 * so SSR + first client render match; a useEffect then upgrades the state
 * from storage on mount, and every subsequent change gets written back.
 *
 * Keyed by caller so different jobs (or different components on the same
 * job) stay isolated. If JSON parse fails for any reason the stored value
 * is discarded silently — the next write will overwrite it.
 */
export function usePersistedState<T>(
  key: string,
  initial: T,
): [T, (next: T) => void] {
  const [state, setState] = useState<T>(initial);
  // Guard the "load from storage" effect from firing on every key change,
  // which could otherwise fight a just-updated state.
  const loadedKey = useRef<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (loadedKey.current === key) return;
    loadedKey.current = key;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw !== null) setState(JSON.parse(raw) as T);
      else setState(initial);
    } catch {
      /* corrupt JSON or storage disabled — stay on initial */
    }
    // We purposely do not include ``initial`` in deps: it would re-run every
    // render for callers passing an inline object/array literal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Only write once we've completed the load pass for this key, so an
    // early render doesn't overwrite stored data with the initial default.
    if (loadedKey.current !== key) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(state));
    } catch {
      /* quota exceeded / storage disabled — persistence degrades silently */
    }
  }, [key, state]);

  return [state, setState];
}
