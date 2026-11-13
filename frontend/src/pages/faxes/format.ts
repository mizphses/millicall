import type { components } from "../../api/schema";

export type FaxRead = components["schemas"]["FaxRead"];

/** 受信 FAX の PDF ダウンロード URL（Cookie 認証の同一オリジン GET）。 */
export function faxPdfUrl(id: number): string {
  return `/api/faxes/${id}/pdf`;
}

/** 受信結果の表示（バッジ文言とトーン）。 */
export function faxResult(fax: Pick<FaxRead, "success" | "result_text">): {
  label: string;
  tone: "success" | "danger";
} {
  if (fax.success) return { label: "成功", tone: "success" };
  return { label: fax.result_text ? `失敗（${fax.result_text}）` : "失敗", tone: "danger" };
}

/** 発信元表示。番号が無ければ相手 FAX の局 ID、それも無ければ「非通知」。 */
export function faxSender(fax: Pick<FaxRead, "from_number" | "remote_station_id">): string {
  if (fax.from_number) return fax.from_number;
  if (fax.remote_station_id) return fax.remote_station_id;
  return "非通知";
}

/** ページ総数（最低 1）。 */
export function totalPages(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}
