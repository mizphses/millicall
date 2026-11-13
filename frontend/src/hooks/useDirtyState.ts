import { useEffect, useRef } from "react";

const activeForms = new Map<symbol, { dirty: boolean; busy: boolean }>();
/** Actions such as logout must check before invalidating the session. */
export function getDirtyState() {
  const states = [...activeForms.values()];
  return {
    dirty: states.some((state) => state.dirty),
    busy: states.some((state) => state.busy),
  };
}
export function useDirtyState(dirty: boolean, busy = false) {
  const key = useRef(Symbol("form"));
  useEffect(() => {
    const id = key.current;
    activeForms.set(id, { dirty, busy });
    return () => {
      activeForms.delete(id);
    };
  }, [dirty, busy]);
}
