import { useEffect, useRef } from "react";
import { useUrlState } from "./useUrlState";

/** Open an object from a shared URL; retain the form while its query refreshes. */
export function useObjectSelection<Row extends { id: number }>(
  rows: Row[] | undefined,
  open: (row: Row) => void,
  close: () => void,
) {
  const [selected, setSelected] = useUrlState("selected");
  const handled = useRef("");
  const pending = useRef<string | null>(null);
  const callbacks = useRef({ open, close });
  callbacks.current = { open, close };
  useEffect(() => {
    if (pending.current !== null) {
      if (selected !== pending.current) return;
      pending.current = null;
    }
    if (selected === handled.current) return;
    if (!selected) {
      handled.current = "";
      callbacks.current.close();
      return;
    }
    const row = rows?.find((value) => String(value.id) === selected);
    if (row) {
      handled.current = selected;
      callbacks.current.open(row);
    }
  }, [selected, rows]);
  return {
    select(id: number) {
      const next = String(id);
      handled.current = next;
      pending.current = selected !== next ? next : null;
      if (selected !== next)
        setSelected(next, { replace: false, ignoreBlocker: true });
    },
    clear() {
      handled.current = "";
      pending.current = selected ? "" : null;
      if (selected) setSelected("", { ignoreBlocker: true });
    },
  };
}
