import type { Edge, Node } from "@xyflow/react";
import type { NodeTypeInfo, WorkflowNodeData } from "./types";

export type WFNode = Node<WorkflowNodeData>;
export interface WorkflowMetadata {
  name: string;
  number: string;
  description: string;
  enabled: boolean;
  default_tts_provider_id?: number | null;
}
export interface WorkflowGraph {
  nodes: WFNode[];
  edges: Edge[];
}
export interface KeyValueRow {
  id: string;
  key: string;
  value: string;
}
export interface ConfigDraft {
  rows?: KeyValueRow[];
  text?: string;
  error: string | null;
}
export type ConfigDrafts = Record<string, ConfigDraft>;

export const CATEGORY_LABEL: Record<string, string> = {
  common: "共通",
  ivr: "音声案内",
  ai_workflow: "AI 応対",
  special: "その他",
};

export function validateWorkflowMetadata(
  form: Pick<WorkflowMetadata, "name" | "number">,
) {
  const errors: Partial<Record<"name" | "number", string>> = {};
  if (!form.name.trim()) errors.name = "名前は必須です";
  else if (form.name.trim().length > 100)
    errors.name = "名前は100文字以内にしてください";
  if (!/^\d{2,6}$/.test(form.number.trim()))
    errors.number = "内線番号は2〜6桁の数字で入力してください";
  return errors;
}

export function readDefinition(
  definition: Record<string, unknown>,
  catalog: NodeTypeInfo[],
  layoutMissing = false,
): WorkflowGraph {
  if (!Array.isArray(definition.nodes) || !Array.isArray(definition.edges))
    throw new Error(
      "ワークフロー定義の形式を確認できません。保存を停止しました。",
    );
  const types = new Map(catalog.map((info) => [info.type, info]));
  const nodes = (definition.nodes as Record<string, unknown>[]).map(
    (raw): WFNode => {
      const info = types.get(String(raw.type));
      // A partial graph must never be saved over nodes that the catalog cannot describe.
      if (!info)
        throw new Error(
          `未対応のノード「${String(raw.type)}」があります。すべてのノードを読み込めるまで保存できません。`,
        );
      const position = raw.position as { x: number; y: number } | null;
      return {
        id: String(raw.id),
        type: "workflowNode",
        position: position ?? { x: 0, y: 0 },
        ariaLabel: `${info.label}。Enter キーで設定を開きます`,
        data: {
          nodeType: info.type,
          label: info.label,
          config: (raw.config ?? {}) as Record<string, unknown>,
          configSchema: info.config_schema,
          outputHandles: info.output_handles,
          dynamicHandles: info.dynamic_handles,
          category: info.category,
        },
      };
    },
  );
  const edges = (definition.edges as Record<string, unknown>[]).map(
    (raw): Edge => ({
      id: String(raw.id),
      source: String(raw.source),
      target: String(raw.target),
      sourceHandle: (raw.sourceHandle as string | null) ?? null,
      targetHandle: (raw.targetHandle as string | null) ?? null,
    }),
  );
  return {
    nodes:
      layoutMissing &&
      nodes.every((node) => node.position.x === 0 && node.position.y === 0)
        ? nodes.map((node, index) => ({
            ...node,
            position: { x: (index % 3) * 240, y: Math.floor(index / 3) * 140 },
          }))
        : nodes,
    edges,
  };
}

export function writeDefinition({ nodes, edges }: WorkflowGraph) {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      config: node.data.config,
      position: { x: node.position.x, y: node.position.y },
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle ?? null,
      targetHandle: edge.targetHandle ?? null,
    })),
  };
}

export function editorSnapshot(
  metadata: WorkflowMetadata,
  graph: WorkflowGraph,
) {
  return JSON.stringify({ ...metadata, definition: writeDefinition(graph) });
}

export function rowsFromValue(value: unknown): KeyValueRow[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value).map(([key, val], index) => ({
    id: `saved-${index}`,
    key,
    value: String(val),
  }));
}

export function validateKeyValueRows(rows: KeyValueRow[]): string | null {
  const keys = new Set<string>();
  for (const row of rows) {
    if (!row.key.trim())
      return "キーを入力してください。不要な行は削除できます。";
    if (keys.has(row.key))
      return `キー「${row.key}」が重複しています。各行に異なるキーを入力してください。`;
    keys.add(row.key);
  }
  return null;
}

export function rowsToValue(rows: KeyValueRow[]): Record<string, string> {
  const error = validateKeyValueRows(rows);
  if (error) throw new Error(error);
  return Object.fromEntries(rows.map((row) => [row.key, row.value]));
}

/** Put the new node near the viewport center without covering an existing node. */
export function placeNewNode(
  preferred: { x: number; y: number },
  nodes: WFNode[],
) {
  const position = { ...preferred };
  while (
    nodes.some(
      (node) =>
        position.x < node.position.x + (node.measured?.width ?? 240) + 24 &&
        position.x + 240 + 24 > node.position.x &&
        position.y < node.position.y + (node.measured?.height ?? 120) + 24 &&
        position.y + 120 + 24 > node.position.y,
    )
  )
    position.y += 180;
  return position;
}
