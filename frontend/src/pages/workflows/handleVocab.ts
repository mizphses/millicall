/**
 * 動的ノード（dtmf_input / intent_detection）の output handle 計算。
 *
 * バックエンドの `output_handles()`（src/millicall/workflows/handles.py）と厳密に
 * 一致させる必要がある。不一致だとエッジの sourceHandle が validate_graph に
 * 弾かれ保存が常に 422 になる（Phase 4b レビュー C1/C2）。DOM 非依存の純関数として
 * 切り出し、ユニットテストで語彙一致を保証する。
 */

/**
 * dtmf_input:
 *   max_digits == 1 → ["0".."9", "timeout"]（単一キーはそのキーで分岐）
 *   max_digits >  1 → ["done", "timeout"]（複数桁は完了/タイムアウトのみ）
 */
export function computeDtmfHandles(config: Record<string, unknown>): string[] {
  const maxDigits = Number(config.max_digits ?? 1);
  if (maxDigits === 1) {
    return [...Array(10).keys()].map(String).concat("timeout");
  }
  return ["done", "timeout"];
}

/**
 * intent_detection: intents の各キー + fallback_intent（重複時は追加しない）。
 * 未分類時の fallback 分岐が描画・配線できないと通話が落ちるため必ず含める。
 */
export function computeIntentHandles(config: Record<string, unknown>): string[] {
  const intents = config.intents as Record<string, string> | undefined;
  const keys = intents ? Object.keys(intents) : [];
  const fallback = String(config.fallback_intent ?? "other");
  return keys.includes(fallback) ? keys : [...keys, fallback];
}
