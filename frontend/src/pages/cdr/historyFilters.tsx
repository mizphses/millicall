import { useRouter } from "@tanstack/react-router";
import { css } from "styled-system/css";
import { button, input } from "styled-system/recipes";
import { useUrlState } from "../../hooks/useUrlState";

export type HistoryFiltersValue = {
  q: string;
  from: string;
  to: string;
  order: "newest" | "oldest";
  direction: string;
};

export function parsePage(value: string): number {
  return /^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 1000000
    ? Number(value) : 1;
}

/** A date picker denotes local calendar days; the API expects UTC instants. */
export function dateBoundary(value: string, nextDay = false): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return undefined;
  if (nextDay) date.setDate(date.getDate() + 1);
  return date.toISOString();
}

export function resultRange(offset: number, count: number): string {
  return count === 0 ? "0 件" : `${offset + 1}–${offset + count} 件目`;
}

export function useHistoryFilters() {
  const router = useRouter();
  const [pageValue, setPageValue] = useUrlState("page", "1");
  const [q] = useUrlState("q", "");
  const [from] = useUrlState("from", "");
  const [to] = useUrlState("to", "");
  const [orderValue] = useUrlState("order", "newest");
  const [direction] = useUrlState("direction", "");
  const order: "newest" | "oldest" = orderValue === "oldest" ? "oldest" : "newest";
  const filters: HistoryFiltersValue = { q, from, to, order, direction };
  return {
    page: parsePage(pageValue), setPage: (value: string) => setPageValue(value, { replace: false }), filters,
    params: {
      q, since: dateBoundary(from), until: dateBoundary(to, true), order,
    },
    apply: (next: HistoryFiltersValue) => {
      const search: Record<string, unknown> = { ...router.state.location.search, ...next };
      delete search.page;
      for (const key of ["q", "from", "to", "direction"]) if (!search[key]) delete search[key];
      if (search.order === "newest") delete search.order;
      void router.navigate({ to: router.state.location.pathname, search: search as never });
    },
  };
}

export function HistoryFilters({ value, onChange, searchLabel, withDirection = false }: {
  value: HistoryFiltersValue;
  onChange: (value: HistoryFiltersValue) => void;
  searchLabel: string;
  withDirection?: boolean;
}) {
  const fieldClass = css({ display: "flex", flexDirection: "column", gap: "1", fontSize: "sm", minW: "0" });
  return (
    <form key={JSON.stringify(value)} aria-label="履歴の絞り込み"
      className={css({ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "3", mb: "4" })}
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        onChange({ q: String(data.get("q") ?? "").trim(), from: String(data.get("from") ?? ""),
          to: String(data.get("to") ?? ""), order: data.get("order") === "oldest" ? "oldest" : "newest",
          direction: String(data.get("direction") ?? "") });
      }}>
      <label className={css({ display: "flex", flexDirection: "column", gap: "1", fontSize: "sm", flex: "1 1 240px", minW: "0" })}>
        {searchLabel}<input name="q" type="search" maxLength={200} defaultValue={value.q} className={input()} />
      </label>
      <label className={fieldClass}>開始日<input name="from" type="date" defaultValue={value.from} className={input()}
        onChange={(event) => { const end = event.currentTarget.form?.elements.namedItem("to") as HTMLInputElement | null; if (end) end.min = event.currentTarget.value; }} /></label>
      <label className={fieldClass}>終了日<input name="to" type="date" min={value.from || undefined} defaultValue={value.to} className={input()} /></label>
      {withDirection ? <label className={fieldClass}>方向<select name="direction" defaultValue={value.direction} className={input()}>
        <option value="">すべて</option><option value="inbound">着信</option><option value="outbound">発信</option>
      </select></label> : null}
      <label className={fieldClass}>並び順<select name="order" defaultValue={value.order} className={input()}>
        <option value="newest">新しい順</option><option value="oldest">古い順</option>
      </select></label>
      <button className={button({ variant: "secondary", size: "sm" })} type="submit">絞り込む</button>
      <button className={button({ variant: "ghost", size: "sm" })} type="button"
        onClick={() => onChange({ q: "", from: "", to: "", order: "newest", direction: "" })}>クリア</button>
    </form>
  );
}
