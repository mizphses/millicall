/**
 * System ページのデータ正規化ヘルパー（テスト可能なピュア関数）。
 *
 * コンテナ / システム情報のレスポンスは動的な JSON なので、
 * 期待するフィールドを安全に取り出して表示用の形に整える。
 */

export type ContainerRow = {
  name: string;
  image: string;
  state: string;
  status: string;
  managed: boolean;
};

/** コンテナ 1 件分の生 JSON を表示用の行に正規化する。 */
export function normalizeContainer(raw: Record<string, unknown>): ContainerRow {
  const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
  return {
    name: str(raw.name),
    image: str(raw.image),
    state: str(raw.state),
    status: str(raw.status),
    // managed フラグは managed / is_managed / restartable いずれか
    managed: Boolean(raw.managed ?? raw.is_managed ?? raw.restartable ?? false),
  };
}

export function normalizeContainers(raw: Record<string, unknown>[]): ContainerRow[] {
  return raw.map(normalizeContainer);
}

/** state 文字列を tone（badge variant）に写像する。 */
export function stateTone(state: string): "success" | "warn" | "danger" | "neutral" {
  const s = state.toLowerCase();
  if (s === "running") return "success";
  if (s === "restarting" || s === "created" || s === "paused") return "warn";
  if (s === "exited" || s === "dead") return "danger";
  return "neutral";
}

/** システム情報 JSON を [ラベル, 値] のペア配列に整形する。 */
export function systemInfoEntries(info: Record<string, unknown>): [string, string][] {
  return Object.entries(info).map(([k, v]) => {
    const label = k
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
    const value =
      v == null
        ? "—"
        : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
    return [label, value];
  });
}
