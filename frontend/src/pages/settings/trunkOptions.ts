import { api } from "../../api/client";
import type { components } from "../../api/schema";

type TrunkRead = components["schemas"]["TrunkRead"];

export async function fetchTrunks(): Promise<TrunkRead[]> {
  const { data, error } = await api.GET("/api/trunks");
  if (error) throw new Error("トランク一覧の取得に失敗しました");
  return data ?? [];
}

/**
 * 「既定の発信トランク」セレクトの選択肢。
 * 先頭は未設定（名前順先頭を使う）。無効・着信専用のトランクも選べるが、
 * その場合は名前順先頭へフォールバックすることをラベルで示す。
 */
export function buildTrunkOptions(
  trunks: Pick<TrunkRead, "id" | "name" | "display_name" | "caller_id" | "enabled" | "outbound_enabled">[],
): { value: string; label: string }[] {
  return [
    { value: "", label: "（未設定: 発信に使うトランクの名前順先頭）" },
    ...[...trunks]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((t) => {
        const usable = t.enabled && t.outbound_enabled;
        const callerId = t.caller_id ? ` / 番号通知 ${t.caller_id}` : "";
        const note = usable ? "" : `（${t.enabled ? "着信専用" : "無効"}のため使われません）`;
        return { value: String(t.id), label: `${t.display_name}（${t.name}${callerId}）${note}` };
      }),
  ];
}
