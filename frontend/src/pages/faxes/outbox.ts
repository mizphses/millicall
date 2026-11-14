import type { components } from "../../api/schema";

export type FaxOutboxRead = components["schemas"]["FaxOutboxRead"];

/** アップロード上限（バックエンドの FAX_MAX_UPLOAD_BYTES と一致させる）。 */
export const FAX_MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

/** 宛先番号の形式（国内番号のみ。バックエンドの is_domestic_fax_number と同じ）。 */
const FAX_NUMBER_RE = /^0[1-9]\d{8,9}$/;
/** 国際番号（010 / 00x）。国内番号と同じ桁数でも送信できない。 */
const INTL_RE = /^(010\d+|00[1-9]\d+)$/;

export function isDomesticFaxNumber(number: string): boolean {
  return FAX_NUMBER_RE.test(number) && !INTL_RE.test(number);
}

/** 入力された宛先番号からハイフン・空白を除く。 */
export function normalizeFaxNumber(value: string): string {
  return value.replace(/[\s\-‐－ー()（）]/g, "");
}

export interface FaxSendFormValues {
  toNumber: string;
  /** 空 = 既定の発信トランク。 */
  trunkName: string;
  file: File | null;
}

export function emptyFaxSendForm(): FaxSendFormValues {
  return { toNumber: "", trunkName: "", file: null };
}

export type FaxSendErrors = Partial<Record<"toNumber" | "file", string>>;

/** 送信フォームの入力検証（送信前にクライアント側で弾ける誤りだけ）。 */
export function validateFaxSendForm(form: FaxSendFormValues): FaxSendErrors {
  const errors: FaxSendErrors = {};
  const number = normalizeFaxNumber(form.toNumber);
  if (!number) errors.toNumber = "宛先の FAX 番号を入力してください";
  else if (!isDomesticFaxNumber(number))
    errors.toNumber = "0 から始まる 10〜11 桁の国内番号を入力してください（国際番号は送信できません）";
  if (!form.file) errors.file = "送信する PDF を選択してください";
  else if (!isPdfFile(form.file)) errors.file = "PDF ファイルを選択してください";
  else if (form.file.size > FAX_MAX_UPLOAD_BYTES) errors.file = "PDF は 20 MB までです";
  else if (form.file.size === 0) errors.file = "PDF ファイルが空です";
  return errors;
}

export function isPdfFile(file: Pick<File, "name" | "type">): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

/** multipart の送信本文。 */
export function buildFaxSendFormData(form: FaxSendFormValues): FormData {
  const data = new FormData();
  if (form.file) data.append("file", form.file, form.file.name);
  data.append("to_number", normalizeFaxNumber(form.toNumber));
  if (form.trunkName) data.append("trunk_name", form.trunkName);
  return data;
}

type Tone = "neutral" | "success" | "warn" | "danger" | "accent";

/** 送信状態のバッジ表示。 */
export function outboxStatus(job: Pick<FaxOutboxRead, "status" | "attempts" | "next_attempt_at">): {
  label: string;
  tone: Tone;
} {
  switch (job.status) {
    case "converting":
      return { label: "変換中", tone: "accent" };
    case "queued":
      return job.attempts > 0 && job.next_attempt_at
        ? { label: "再試行待ち", tone: "warn" }
        : { label: "送信待ち", tone: "neutral" };
    case "sending":
      return { label: "送信中", tone: "accent" };
    case "sent":
      return { label: "送信済み", tone: "success" };
    case "failed":
      return { label: "失敗", tone: "danger" };
    case "cancelled":
      return { label: "取消", tone: "neutral" };
    default:
      return { label: job.status, tone: "neutral" };
  }
}

/** 再送できるか（サーバーが状態・再送回数の上限・TIFF の有無から判定した値）。 */
export function canRetry(job: Pick<FaxOutboxRead, "can_retry">): boolean {
  return job.can_retry;
}

export function canCancel(job: Pick<FaxOutboxRead, "status">): boolean {
  return job.status === "queued" || job.status === "converting";
}

/** 変換中・送信待ち・送信中のジョブがあれば一覧を自動更新する。 */
export function hasActiveJobs(jobs: Pick<FaxOutboxRead, "status">[]): boolean {
  return jobs.some(
    (j) => j.status === "converting" || j.status === "queued" || j.status === "sending",
  );
}

/** 送信 FAX の元 PDF の URL（Cookie 認証の同一オリジン GET）。 */
export function outboxPdfUrl(id: number): string {
  return `/api/faxes/outbox/${id}/pdf`;
}

/** API のエラー応答（detail）を表示用の文言にする。 */
export function apiErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return fallback;
}
