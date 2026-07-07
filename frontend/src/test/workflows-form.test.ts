/**
 * ワークフロー関連のユニットテスト（Phase 4b Task 10）。
 * 既存テストと同じ node 環境・vitest パターンに準拠。
 * DOM が不要なロジック（バリデーション・クエリキー・型変換）のみテストする。
 */
import { describe, it, expect } from "vitest";

import {
  WORKFLOWS_KEY,
  WORKFLOW_NODE_TYPES_KEY,
  workflowKey,
  EXTENSIONS_KEY,
  TRUNKS_KEY,
  AGENTS_KEY,
  PROVIDERS_KEY,
  ROUTES_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
} from "../queryKeys";
import { computeDtmfHandles, computeIntentHandles } from "../pages/workflows/handleVocab";

// ─────────────────────────────────────────────────────────
// クエリキー衝突テスト
// ─────────────────────────────────────────────────────────

describe("queryKeys — ワークフローキーの衝突なし", () => {
  const allTopLevelKeys = [
    EXTENSIONS_KEY[0],
    TRUNKS_KEY[0],
    AGENTS_KEY[0],
    PROVIDERS_KEY[0],
    ROUTES_KEY[0],
    CONTACTS_KEY[0],
    CDR_KEY[0],
    CALL_MESSAGES_KEY[0],
  ] as const;

  it("WORKFLOWS_KEY は既存の一覧キーと衝突しない", () => {
    expect(allTopLevelKeys as readonly string[]).not.toContain(WORKFLOWS_KEY[0]);
  });

  it("WORKFLOW_NODE_TYPES_KEY は既存の一覧キーと衝突しない", () => {
    expect(allTopLevelKeys as readonly string[]).not.toContain(WORKFLOW_NODE_TYPES_KEY[0]);
  });

  it("WORKFLOWS_KEY と WORKFLOW_NODE_TYPES_KEY は互いに異なる", () => {
    expect(WORKFLOWS_KEY[0]).not.toBe(WORKFLOW_NODE_TYPES_KEY[0]);
  });

  it("workflowKey(id) はトップレベルが WORKFLOWS_KEY と一致し、2 要素", () => {
    const key = workflowKey(42);
    expect(key[0]).toBe(WORKFLOWS_KEY[0]);
    expect(key[1]).toBe(42);
    expect(key).toHaveLength(2);
  });

  it("異なる id の workflowKey は衝突しない", () => {
    expect(workflowKey(1).join("/")).not.toBe(workflowKey(2).join("/"));
  });
});

// ─────────────────────────────────────────────────────────
// ワークフロー作成フォームバリデーションロジック
// ─────────────────────────────────────────────────────────

/** WorkflowsPage の validateForm と同じロジックをここでテストする。 */
function validateWorkflowForm(form: {
  name: string;
  number: string;
  description: string;
}): Partial<Record<"name" | "number", string>> {
  const errors: Partial<Record<"name" | "number", string>> = {};
  if (!form.name.trim()) errors.name = "名前は必須です";
  if (form.name.trim().length > 100) errors.name = "名前は100文字以内にしてください";
  if (!form.number.trim()) errors.number = "番号は必須です";
  if (!/^[0-9*#]{1,30}$/.test(form.number.trim()))
    errors.number = "番号は数字・*・# のみ（最大30文字）";
  return errors;
}

describe("validateWorkflowForm", () => {
  function form(overrides: Partial<{ name: string; number: string; description: string }>) {
    return { name: "受付フロー", number: "0312345678", description: "", ...overrides };
  }

  it("有効なフォームはエラーなし", () => {
    expect(Object.keys(validateWorkflowForm(form({})))).toHaveLength(0);
  });

  it("name が空ならエラー", () => {
    expect(validateWorkflowForm(form({ name: "" })).name).toBeTruthy();
  });

  it("name が空白のみならエラー", () => {
    expect(validateWorkflowForm(form({ name: "   " })).name).toBeTruthy();
  });

  it("name が 101 文字ならエラー", () => {
    expect(validateWorkflowForm(form({ name: "a".repeat(101) })).name).toBeTruthy();
  });

  it("name が 100 文字はエラーなし", () => {
    expect(validateWorkflowForm(form({ name: "a".repeat(100) })).name).toBeUndefined();
  });

  it("number が空ならエラー", () => {
    expect(validateWorkflowForm(form({ number: "" })).number).toBeTruthy();
  });

  it("number に英字が含まれるとエラー", () => {
    expect(validateWorkflowForm(form({ number: "abc123" })).number).toBeTruthy();
  });

  it("number が数字・*・# のみなら有効", () => {
    expect(validateWorkflowForm(form({ number: "0*#123" })).number).toBeUndefined();
  });

  it("number が 31 文字を超えるとエラー", () => {
    expect(validateWorkflowForm(form({ number: "1".repeat(31) })).number).toBeTruthy();
  });

  it("number が 30 文字はエラーなし", () => {
    expect(validateWorkflowForm(form({ number: "1".repeat(30) })).number).toBeUndefined();
  });
});

// ─────────────────────────────────────────────────────────
// バックエンドノード ↔ xyflow ノード変換ロジック
// ─────────────────────────────────────────────────────────

import type { NodeTypeInfo } from "../pages/workflows/types";

/** WorkflowEditorPage の backendNodeToXyflow と同じロジックをここで検証。 */
function backendNodeToXyflow(
  backendNode: Record<string, unknown>,
  nodeTypeInfo: NodeTypeInfo,
) {
  const pos = (backendNode.position as { x: number; y: number } | undefined) ?? { x: 0, y: 0 };
  const config = (backendNode.config as Record<string, unknown>) ?? {};
  return {
    id: String(backendNode.id),
    type: "workflowNode",
    position: { x: pos.x, y: pos.y },
    data: {
      nodeType: nodeTypeInfo.type,
      label: nodeTypeInfo.label,
      config,
      configSchema: nodeTypeInfo.config_schema,
      outputHandles: nodeTypeInfo.output_handles,
      dynamicHandles: nodeTypeInfo.dynamic_handles,
    },
  };
}

const MOCK_NODE_TYPE_INFO: NodeTypeInfo = {
  type: "play_audio",
  category: "common",
  label: "音声再生",
  config_schema: [
    { key: "tts_text", type: "textarea", label: "読み上げテキスト", required: true },
    { key: "tts_provider_id", type: "provider_ref", label: "TTSプロバイダ", required: false, default: null, provider_type: "tts" },
    { key: "file_path", type: "string", label: "再生ファイル", required: false, default: "" },
  ],
  output_handles: ["next"],
  dynamic_handles: false,
};

describe("backendNodeToXyflow", () => {
  it("id を文字列に変換する", () => {
    const result = backendNodeToXyflow({ id: 42, type: "play_audio", config: {} }, MOCK_NODE_TYPE_INFO);
    expect(result.id).toBe("42");
  });

  it("type は常に 'workflowNode'", () => {
    const result = backendNodeToXyflow({ id: "n1", type: "play_audio", config: {} }, MOCK_NODE_TYPE_INFO);
    expect(result.type).toBe("workflowNode");
  });

  it("position が未指定なら {x:0, y:0} になる", () => {
    const result = backendNodeToXyflow({ id: "n1", type: "play_audio", config: {} }, MOCK_NODE_TYPE_INFO);
    expect(result.position).toEqual({ x: 0, y: 0 });
  });

  it("position が指定されていれば保全される", () => {
    const result = backendNodeToXyflow(
      { id: "n1", type: "play_audio", config: {}, position: { x: 120, y: 340 } },
      MOCK_NODE_TYPE_INFO,
    );
    expect(result.position).toEqual({ x: 120, y: 340 });
  });

  it("data.nodeType がバックエンドの type と一致する", () => {
    const result = backendNodeToXyflow({ id: "n1", type: "play_audio", config: { tts_text: "こんにちは" } }, MOCK_NODE_TYPE_INFO);
    expect(result.data.nodeType).toBe("play_audio");
  });

  it("config が data.config に格納される", () => {
    const result = backendNodeToXyflow(
      { id: "n1", type: "play_audio", config: { tts_text: "テスト" } },
      MOCK_NODE_TYPE_INFO,
    );
    expect(result.data.config).toEqual({ tts_text: "テスト" });
  });

  it("outputHandles が NodeTypeInfo から引き継がれる", () => {
    const result = backendNodeToXyflow({ id: "n1", type: "play_audio", config: {} }, MOCK_NODE_TYPE_INFO);
    expect(result.data.outputHandles).toEqual(["next"]);
  });
});

/** xyflowNodeToBackend の等価ロジック */
function xyflowNodeToBackend(node: {
  id: string;
  position: { x: number; y: number };
  data: { nodeType: string; config: Record<string, unknown> };
}): Record<string, unknown> {
  return {
    id: node.id,
    type: node.data.nodeType,
    config: node.data.config,
    position: { x: node.position.x, y: node.position.y },
  };
}

describe("xyflowNodeToBackend", () => {
  it("id・type・config・position を正しくマッピングする", () => {
    const result = xyflowNodeToBackend({
      id: "play_audio_123",
      position: { x: 100, y: 200 },
      data: { nodeType: "play_audio", config: { tts_text: "お待ちください" } },
    });
    expect(result).toEqual({
      id: "play_audio_123",
      type: "play_audio",
      config: { tts_text: "お待ちください" },
      position: { x: 100, y: 200 },
    });
  });

  it("round-trip で position が保全される", () => {
    const backendNode = { id: "n1", type: "play_audio", config: { tts_text: "hi" }, position: { x: 333, y: 444 } };
    const xyflow = backendNodeToXyflow(backendNode, MOCK_NODE_TYPE_INFO);
    const roundTripped = xyflowNodeToBackend(xyflow);
    expect(roundTripped.position).toEqual({ x: 333, y: 444 });
  });
});

// ─────────────────────────────────────────────────────────
// 警告バッジのロジック（WorkflowsPage の warnings 表示判定）
// ─────────────────────────────────────────────────────────

describe("警告バッジ表示判定", () => {
  function hasWarnings(warnings: string[] | undefined): boolean {
    return (warnings ?? []).length > 0;
  }

  it("warnings が undefined なら false", () => {
    expect(hasWarnings(undefined)).toBe(false);
  });

  it("warnings が空配列なら false", () => {
    expect(hasWarnings([])).toBe(false);
  });

  it("warnings に要素があれば true", () => {
    expect(hasWarnings(["node 'x' is unreachable from start"])).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────
// 動的ハンドル計算 — バックエンド output_handles() との語彙一致（C1/C2 回帰）
// ─────────────────────────────────────────────────────────

describe("computeDtmfHandles — バックエンド語彙一致", () => {
  it("max_digits==1 は 0..9 + timeout", () => {
    expect(computeDtmfHandles({ max_digits: 1 })).toEqual([
      "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "timeout",
    ]);
  });

  it("max_digits>1 は done + timeout", () => {
    expect(computeDtmfHandles({ max_digits: 4 })).toEqual(["done", "timeout"]);
  });

  it("max_digits 未指定は 1 桁扱い", () => {
    expect(computeDtmfHandles({})).toContain("timeout");
    expect(computeDtmfHandles({})).toContain("0");
  });
});

describe("computeIntentHandles — fallback_intent を必ず含む", () => {
  it("intents キー + fallback_intent", () => {
    expect(
      computeIntentHandles({ intents: { sales: "営業", support: "サポート" }, fallback_intent: "other" }),
    ).toEqual(["sales", "support", "other"]);
  });

  it("fallback が intents に含まれる場合は重複させない", () => {
    expect(
      computeIntentHandles({ intents: { other: "その他", sales: "営業" }, fallback_intent: "other" }),
    ).toEqual(["other", "sales"]);
  });

  it("fallback_intent 未指定は 'other' を補う", () => {
    expect(computeIntentHandles({ intents: { a: "A" } })).toEqual(["a", "other"]);
  });
});
