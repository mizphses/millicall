import { describe, expect, it } from "vitest";
import {
  editorSnapshot,
  placeNewNode,
  readDefinition,
  rowsFromValue,
  rowsToValue,
  validateKeyValueRows,
  validateWorkflowMetadata,
  writeDefinition,
} from "../pages/workflows/editorState";
import type { NodeTypeInfo } from "../pages/workflows/types";

const type: NodeTypeInfo = {
  type: "intent_detection",
  category: "ai_workflow",
  label: "意図を判定",
  config_schema: [],
  output_handles: [],
  dynamic_handles: true,
};
const metadata = {
  name: "受付",
  number: "300",
  description: "",
  enabled: true,
  default_tts_provider_id: null,
};
const definition = {
  nodes: [
    {
      id: "n1",
      type: type.type,
      config: { intents: { sales: "営業", support: "支援" } },
      position: { x: 120, y: 340 },
    },
  ],
  edges: [
    {
      id: "e1",
      source: "n1",
      target: "n1",
      sourceHandle: "sales",
      targetHandle: "target",
    },
  ],
};

describe("workflow metadata uses the server's extension constraints", () => {
  it.each(["30", "300", "123456"])("accepts %s", (number) =>
    expect(validateWorkflowMetadata({ ...metadata, number })).toEqual({}),
  );
  it.each(["", "1", "1234567", "0312345678", "*30", "１２３", "abc"])(
    "rejects %s",
    (number) =>
      expect(
        validateWorkflowMetadata({ ...metadata, number }).number,
      ).toBeTruthy(),
  );
  it("rejects empty or overlong names", () => {
    expect(
      validateWorkflowMetadata({ ...metadata, name: "  " }).name,
    ).toBeTruthy();
    expect(
      validateWorkflowMetadata({ ...metadata, name: "a".repeat(101) }).name,
    ).toBeTruthy();
  });
});

describe("lossless workflow editing", () => {
  it("preserves positions, node configs, and edge handles through a round trip", () =>
    expect(writeDefinition(readDefinition(definition, [type]))).toEqual(
      definition,
    ));
  it("rejects unknown node types rather than silently dropping them before save", () => {
    expect(() => readDefinition(definition, [])).toThrow("未対応のノード");
    expect(() =>
      readDefinition(
        {
          ...definition,
          nodes: [...definition.nodes, { id: "unknown", type: "new_type" }],
        },
        [type],
      ),
    ).toThrow("new_type");
  });
  it("rejects malformed definitions instead of replacing them with an empty graph", () =>
    expect(() => readDefinition({}, [type])).toThrow("形式"));
  it("does not mark selection or measured dimensions as an unsaved edit", () => {
    const graph = readDefinition(definition, [type]);
    const selected = {
      ...graph,
      nodes: graph.nodes.map((node) => ({
        ...node,
        selected: true,
        measured: { width: 180, height: 100 },
        data: { ...node.data, onOpen: () => undefined },
      })),
    };
    expect(editorSnapshot(metadata, selected)).toBe(
      editorSnapshot(metadata, graph),
    );
  });
  it("marks position, config, and metadata changes as edits", () => {
    const graph = readDefinition(definition, [type]);
    const original = editorSnapshot(metadata, graph);
    expect(editorSnapshot({ ...metadata, name: "新しい受付" }, graph)).not.toBe(
      original,
    );
    expect(
      editorSnapshot(metadata, {
        ...graph,
        nodes: graph.nodes.map((node) => ({
          ...node,
          position: { x: 1, y: 2 },
        })),
      }),
    ).not.toBe(original);
    expect(
      editorSnapshot(metadata, {
        ...graph,
        nodes: graph.nodes.map((node) => ({
          ...node,
          data: { ...node.data, config: {} },
        })),
      }),
    ).not.toBe(original);
  });
  it("generating a preview does not mutate the original graph or its undo snapshot", () => {
    const original = readDefinition(definition, [type]);
    const snapshot = editorSnapshot(metadata, original);
    const generated = readDefinition(
      { nodes: [{ id: "new", type: type.type, config: {} }], edges: [] },
      [type],
      true,
    );
    expect(generated.nodes[0].id).toBe("new");
    expect(editorSnapshot(metadata, original)).toBe(snapshot);
  });
});

describe("key/value row drafts", () => {
  it("retains both rows and values when a key is changed to a duplicate", () => {
    const rows = rowsFromValue({ sales: "営業", support: "支援" });
    const edited = rows.map((row, index) =>
      index === 1 ? { ...row, key: "sales" } : row,
    );
    expect(edited).toHaveLength(2);
    expect(edited.map((row) => row.value)).toEqual(["営業", "支援"]);
    expect(edited.map((row) => row.id)).toEqual(rows.map((row) => row.id));
    expect(validateKeyValueRows(edited)).toContain("重複");
    expect(() => rowsToValue(edited)).toThrow("重複");
    const corrected = edited.map((row, index) =>
      index === 1 ? { ...row, key: "help" } : row,
    );
    expect(rowsToValue(corrected)).toEqual({ sales: "営業", help: "支援" });
  });
  it("keeps several new empty rows distinct and refuses to serialize them", () => {
    const rows = [
      { id: "draft-a", key: "", value: "first" },
      { id: "draft-b", key: "", value: "second" },
    ];
    expect(validateKeyValueRows(rows)).toContain("キーを入力");
    expect(() => rowsToValue(rows)).toThrow();
    expect(rows.map((row) => row.value)).toEqual(["first", "second"]);
  });
});

describe("new node placement", () => {
  it("avoids covering existing nodes without moving the existing graph", () => {
    const graph = readDefinition(definition, [type]);
    const original = structuredClone(graph.nodes[0].position);
    const placed = placeNewNode(original, graph.nodes);
    expect(placed.y).toBeGreaterThan(original.y + 120);
    expect(graph.nodes[0].position).toEqual(original);
  });
  it("keeps a free viewport-center position", () => {
    expect(
      placeNewNode(
        { x: 800, y: 200 },
        readDefinition(definition, [type]).nodes,
      ),
    ).toEqual({ x: 800, y: 200 });
  });
});
