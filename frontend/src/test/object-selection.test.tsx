// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useObjectSelection } from "../hooks/useObjectSelection";

const location = vi.hoisted(() => ({ selected: "", writes: [] as string[] }));
vi.mock("../hooks/useUrlState", () => ({
  useUrlState: () => [
    location.selected,
    (value: string) => location.writes.push(value),
  ],
}));
const opened = vi.fn();
const closed = vi.fn();
let root: Root,
  host: HTMLDivElement,
  controller: ReturnType<typeof useObjectSelection<{ id: number }>>;
function Harness({
  rows = [{ id: 1 }, { id: 2 }],
}: {
  rows?: { id: number }[];
}) {
  controller = useObjectSelection(
    rows,
    (row) => {
      opened(row.id);
      controller.select(row.id);
    },
    () => {
      closed();
      controller.clear();
    },
  );
  return null;
}
beforeEach(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  location.selected = "";
  location.writes = [];
  opened.mockClear();
  closed.mockClear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});
it("closes a restored object when browser Back removes selection", () => {
  location.selected = "1";
  act(() => root.render(<Harness />));
  expect(opened).toHaveBeenCalledWith(1);
  expect(location.writes).toEqual([]);
  location.selected = "";
  act(() => root.render(<Harness />));
  expect(closed).toHaveBeenCalledTimes(1);
});
it("does not reopen stale selection during query refresh while URL clear is pending", () => {
  location.selected = "1";
  act(() => root.render(<Harness />));
  act(() => controller.clear());
  act(() => root.render(<Harness rows={[{ id: 1 }]} />));
  expect(opened).toHaveBeenCalledTimes(1);
  expect(location.writes).toEqual([""]);
  location.selected = "";
  act(() => root.render(<Harness />));
  location.selected = "2";
  act(() => root.render(<Harness />));
  expect(opened).toHaveBeenLastCalledWith(2);
});
it("clearing an already empty selection does not prevent a later shared object from opening", () => {
  act(() => root.render(<Harness />));
  act(() => controller.clear());
  location.selected = "2";
  act(() => root.render(<Harness />));
  expect(opened).toHaveBeenCalledWith(2);
});
