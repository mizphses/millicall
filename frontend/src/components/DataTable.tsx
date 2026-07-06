import type { ReactNode } from "react";

import { css } from "styled-system/css";
import { panel, table } from "styled-system/recipes";

export interface Column<Row> {
  /** 行内で一意なキー。 */
  key: string;
  /** ヘッダ表示（日本語）。 */
  header: ReactNode;
  /** セル描画。省略時は row[key] を文字列表示。 */
  render?: (row: Row) => ReactNode;
  /** セル幅（トークン経由でなく列幅指定用に許容）。 */
  width?: string;
  align?: "left" | "right" | "center";
}

interface DataTableProps<Row> {
  columns: Column<Row>[];
  rows: Row[];
  /** 各行の React key を得る。 */
  rowKey: (row: Row) => string | number;
  /** ローディング中フラグ。 */
  loading?: boolean;
  /** データ 0 件時の表示。 */
  emptyMessage?: string;
  /** 行クリック（任意）。 */
  onRowClick?: (row: Row) => void;
}

/**
 * 汎用データテーブル。columns 定義 + rows で描画する。
 * 後続タスクの一覧画面はすべてこれを使う（契約）。
 */
export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  loading = false,
  emptyMessage = "データがありません",
  onRowClick,
}: DataTableProps<Row>) {
  return (
    <div className={panel()} style={{ overflow: "hidden" }}>
      <div className={css({ overflowX: "auto" })}>
        <table className={table()}>
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col.key} style={{ width: col.width, textAlign: col.align ?? "left" }}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={columns.length} className={css({ color: "text.muted", textAlign: "center" })}>
                  読み込み中…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className={css({ color: "text.muted", textAlign: "center" })}>
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={onRowClick ? css({ cursor: "pointer" }) : undefined}
                >
                  {columns.map((col) => (
                    <td key={col.key} style={{ textAlign: col.align ?? "left" }}>
                      {col.render ? col.render(row) : String((row as Record<string, unknown>)[col.key] ?? "")}
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
