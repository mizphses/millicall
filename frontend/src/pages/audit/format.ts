/**
 * 監査ログの表示整形ヘルパー（テスト可能なピュア関数）。
 */

import type { components } from "../../api/schema.d";

export type AuditLogRead = components["schemas"]["AuditLogRead"];

/** ISO 日時を「YYYY-MM-DD HH:mm:ss」（ローカル）に整形する。 */
export function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

/** target_type / target_id を "type#id" 形式に整形する（両方空なら "—"）。 */
export function formatTarget(log: AuditLogRead): string {
  if (log.target_type && log.target_id) return `${log.target_type}#${log.target_id}`;
  if (log.target_type) return log.target_type;
  if (log.target_id) return log.target_id;
  return "—";
}

/** ページネーションの前後可否を判定する。 */
export function canPrev(offset: number): boolean {
  return offset > 0;
}

/** 取得件数が limit と一致すれば次ページがある可能性が高い。 */
export function canNext(rowCount: number, limit: number): boolean {
  return rowCount >= limit;
}

/** offset を limit 単位で前後に動かす（0 未満にはしない）。 */
export function stepOffset(offset: number, limit: number, dir: 1 | -1): number {
  return Math.max(0, offset + dir * limit);
}
