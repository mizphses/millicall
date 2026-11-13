import { useMemo, type ReactNode } from "react";
import { css } from "styled-system/css";
import { button, input, panel, table } from "styled-system/recipes";
import { useUrlState } from "../hooks/useUrlState";

export interface Column<Row> {
  key: string;
  header: ReactNode;
  render?: (row: Row) => ReactNode;
  width?: string;
  align?: "left" | "right" | "center";
  sortValue?: (row: Row) => string | number;
  sortable?: boolean;
}
interface DataTableProps<Row> {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string | number;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  emptyMessage?: string;
  onRowClick?: (row: Row) => void;
  searchable?: boolean;
  searchPlaceholder?: string;
  searchText?: (row: Row) => string;
  storageKey?: string;
}
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  loading = false,
  error,
  onRetry,
  emptyMessage = "データがありません",
  onRowClick,
  searchable = false,
  searchPlaceholder = "一覧を検索",
  searchText,
  storageKey = "",
}: DataTableProps<Row>) {
  const [query, setQuery] = useUrlState(storageKey ? `${storageKey}_q` : "q");
  const [sort, setSort] = useUrlState(
    storageKey ? `${storageKey}_sort` : "sort",
  );
  const [sortKey, direction] = sort.split(":");
  const visibleRows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const filtered =
      searchable && needle
        ? rows.filter((row) =>
            (searchText
              ? searchText(row)
              : Object.values(row as Record<string, unknown>)
                  .filter((value) => typeof value !== "object")
                  .join(" ")
            )
              .toLocaleLowerCase()
              .includes(needle),
          )
        : [...rows];
    const column = columns.find((c) => c.key === sortKey);
    if (searchable && column)
      filtered.sort((a, b) => {
        const av = column.sortValue
          ? column.sortValue(a)
          : (a as Record<string, unknown>)[column.key];
        const bv = column.sortValue
          ? column.sortValue(b)
          : (b as Record<string, unknown>)[column.key];
        const result =
          typeof av === "number" && typeof bv === "number"
            ? av - bv
            : String(av ?? "").localeCompare(String(bv ?? ""), "ja", {
                numeric: true,
              });
        return direction === "desc" ? -result : result;
      });
    return filtered;
  }, [rows, columns, query, searchable, searchText, sortKey, direction]);
  return (
    <div className={panel()} style={{ overflow: "hidden", minWidth: 0 }}>
      {searchable && (
        <div
          className={css({
            p: "3",
            display: "flex",
            flexWrap: "wrap",
            gap: "3",
            alignItems: "center",
            borderBottom: "1px solid",
            borderColor: "border",
          })}
        >
          <input
            type="search"
            aria-label={searchPlaceholder}
            placeholder={searchPlaceholder}
            className={input()}
            style={{ maxWidth: 360 }}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <span
            role="status"
            className={css({ color: "text.muted", fontSize: "sm" })}
          >
            {loading
              ? "読み込み中…"
              : error
                ? "取得できません"
                : `${visibleRows.length} 件 / 全 ${rows.length} 件`}
          </span>
        </div>
      )}
      {columns.length > 3 && (
        <p
          className={css({
            display: "none",
            px: "3",
            pb: "2",
            fontSize: "sm",
            color: "text.muted",
            "@media (max-width: 860px)": { display: "block" },
          })}
        >
          表を横にスクロールすると、残りの項目と操作を表示できます。
        </p>
      )}
      <div
        className={css({ overflowX: "auto" })}
        tabIndex={0}
        role="region"
        aria-label="一覧表（横にスクロールできます）"
        aria-busy={loading}
      >
        <table
          className={table()}
          style={{ minWidth: columns.length > 3 ? 640 : undefined }}
        >
          <thead>
            <tr>
              {columns.map((col) => {
                const canSort =
                  searchable &&
                  col.sortable !== false &&
                  !col.key.startsWith("_") &&
                  !["actions", "action"].includes(col.key) &&
                  !!col.header;
                return (
                  <th
                    key={col.key}
                    style={{ width: col.width, textAlign: col.align ?? "left" }}
                    scope="col"
                    aria-sort={
                      canSort && sortKey === col.key
                        ? direction === "desc"
                          ? "descending"
                          : "ascending"
                        : undefined
                    }
                  >
                    {canSort ? (
                      <button
                        type="button"
                        className={css({
                          cursor: "pointer",
                          display: "inline-flex",
                          gap: "1",
                          alignItems: "center",
                          minHeight: "24px",
                          _hover: { color: "accent.text" },
                        })}
                        onClick={() =>
                          setSort(
                            `${col.key}:${sortKey === col.key && direction !== "desc" ? "desc" : "asc"}`,
                          )
                        }
                      >
                        {col.header}
                        <span aria-hidden>
                          {sortKey === col.key
                            ? direction === "desc"
                              ? " ↓"
                              : " ↑"
                            : " ↕"}
                        </span>
                      </button>
                    ) : (
                      col.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {error ? (
              <tr>
                <td colSpan={columns.length}>
                  <div
                    role="alert"
                    className={css({ p: "3", color: "danger.text" })}
                  >
                    一覧を取得できませんでした。接続状況を確認して再試行してください。
                    {onRetry && (
                      <button
                        type="button"
                        onClick={onRetry}
                        className={button({ variant: "secondary", size: "sm" })}
                      >
                        再試行
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ) : loading ? (
              <tr>
                <td colSpan={columns.length}>
                  <p
                    role="status"
                    className={css({
                      textAlign: "center",
                      color: "text.muted",
                    })}
                  >
                    読み込み中…
                  </p>
                </td>
              </tr>
            ) : visibleRows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>
                  <p
                    role="status"
                    className={css({
                      textAlign: "center",
                      color: "text.muted",
                    })}
                  >
                    {query && searchable
                      ? "検索条件に一致するデータがありません"
                      : emptyMessage}
                  </p>
                </td>
              </tr>
            ) : (
              visibleRows.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if (
                            event.target === event.currentTarget &&
                            ["Enter", " "].includes(event.key)
                          ) {
                            event.preventDefault();
                            onRowClick(row);
                          }
                        }
                      : undefined
                  }
                  className={
                    onRowClick ? css({ cursor: "pointer" }) : undefined
                  }
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      style={{ textAlign: col.align ?? "left" }}
                    >
                      {col.render
                        ? col.render(row)
                        : String(
                            (row as Record<string, unknown>)[col.key] ?? "",
                          )}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
