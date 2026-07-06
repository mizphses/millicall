/** CDR 表示整形の純関数。DOM 非依存で vitest 対象。 */

/**
 * 通話秒数を mm:ss 形式へ整形する。
 * 負値は 0 とみなす。60 分以上は分がそのまま桁上がりする（例: 3661 → "61:01"）。
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "00:00";
  const total = Math.floor(seconds);
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

/**
 * バックエンドの naive datetime 文字列（例: "2026-07-05T10:00:00"）を
 * ブラウザロケールの日時表示へ整形する。null/空は "-"。
 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** latency_ms をバッジ表示用に整形する。null は "-"。 */
export function formatLatency(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "-";
  return `${Math.round(ms)}ms`;
}
