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
  NUMBER_PLAN_KEY,
  RING_GROUPS_KEY,
  CONTACTS_KEY,
  CDR_KEY,
  CALL_MESSAGES_KEY,
} from "../queryKeys";
import {
  computeDtmfHandles,
  computeIntentHandles,
} from "../pages/workflows/handleVocab";

// ─────────────────────────────────────────────────────────
// クエリキー衝突テスト
// ─────────────────────────────────────────────────────────

describe("queryKeys — ワークフローキーの衝突なし", () => {
  const allTopLevelKeys = [
    EXTENSIONS_KEY[0],
    TRUNKS_KEY[0],
    AGENTS_KEY[0],
    PROVIDERS_KEY[0],
    NUMBER_PLAN_KEY[0],
    RING_GROUPS_KEY[0],
    CONTACTS_KEY[0],
    CDR_KEY[0],
    CALL_MESSAGES_KEY[0],
  ] as const;

  it("WORKFLOWS_KEY は既存の一覧キーと衝突しない", () => {
    expect(allTopLevelKeys as readonly string[]).not.toContain(
      WORKFLOWS_KEY[0],
    );
  });

  it("WORKFLOW_NODE_TYPES_KEY は既存の一覧キーと衝突しない", () => {
    expect(allTopLevelKeys as readonly string[]).not.toContain(
      WORKFLOW_NODE_TYPES_KEY[0],
    );
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

describe("computeDtmfHandles — バックエンド語彙一致", () => {
  it("max_digits==1 は 0..9 + timeout", () => {
    expect(computeDtmfHandles({ max_digits: 1 })).toEqual([
      "0",
      "1",
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
      "timeout",
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
      computeIntentHandles({
        intents: { sales: "営業", support: "サポート" },
        fallback_intent: "other",
      }),
    ).toEqual(["sales", "support", "other"]);
  });

  it("fallback が intents に含まれる場合は重複させない", () => {
    expect(
      computeIntentHandles({
        intents: { other: "その他", sales: "営業" },
        fallback_intent: "other",
      }),
    ).toEqual(["other", "sales"]);
  });

  it("fallback_intent 未指定は 'other' を補う", () => {
    expect(computeIntentHandles({ intents: { a: "A" } })).toEqual([
      "a",
      "other",
    ]);
  });
});
