/**
 * ワークフロー GUI エディタ（Phase 4b Task 10）。
 * xyflow ReactFlow ベースのビジュアルエディタ。
 *
 * 位置の永続化方式: バックエンドの _NodeBase は
 * `position: Position | None = None` を正式フィールドとして保持するため、
 * xyflow ノードの position: {x, y} を各ノードオブジェクトにそのまま格納する。
 * round-trip で完全保全される（backend変更不要）。
 */
import "@xyflow/react/dist/style.css";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { AlertTriangle, ArrowLeft, Save, Sparkles, X } from "lucide-react";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { WORKFLOWS_KEY, WORKFLOW_NODE_TYPES_KEY, workflowKey } from "../queryKeys";
import { useToast } from "../toast/ToastProvider";
import { ConfigPanel } from "./workflows/ConfigPanel";
import { WorkflowNodeComponent } from "./workflows/WorkflowNode";
import type { NodeTypeInfo, WorkflowNodeData } from "./workflows/types";

type WorkflowRead = components["schemas"]["WorkflowRead"];

/** xyflow Node type with our data */
type WFNode = Node<WorkflowNodeData>;

// ─────────────────────────────────────────────────────────
// カスタムノードタイプ登録（コンポーネント外で安定した参照）
// ─────────────────────────────────────────────────────────
const NODE_TYPES: NodeTypes = {
  workflowNode: WorkflowNodeComponent as unknown as NodeTypes[string],
};

// ─────────────────────────────────────────────────────────
// カテゴリ日本語名
// ─────────────────────────────────────────────────────────
const CATEGORY_LABEL: Record<string, string> = {
  common: "共通",
  ivr: "IVR",
  ai_workflow: "AI ワークフロー",
  special: "特殊",
};

// ─────────────────────────────────────────────────────────
// バックエンドノード ↔ xyflow ノード変換ユーティリティ
// ─────────────────────────────────────────────────────────

function backendNodeToXyflow(
  backendNode: Record<string, unknown>,
  nodeTypeInfo: NodeTypeInfo,
): WFNode {
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

function xyflowNodeToBackend(node: WFNode): Record<string, unknown> {
  return {
    id: node.id,
    type: node.data.nodeType,
    config: node.data.config,
    position: { x: node.position.x, y: node.position.y },
  };
}

function backendEdgeToXyflow(edge: Record<string, unknown>): Edge {
  return {
    id: String(edge.id),
    source: String(edge.source),
    target: String(edge.target),
    sourceHandle: (edge.sourceHandle as string | null) ?? null,
    targetHandle: (edge.targetHandle as string | null) ?? null,
    label: (edge.label as string | undefined) ?? undefined,
  };
}

function xyflowEdgeToBackend(edge: Edge): Record<string, unknown> {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle ?? null,
    targetHandle: edge.targetHandle ?? null,
  };
}

/** 位置情報がないノードを縦並びでレイアウトする（AI 生成時のフォールバック）。 */
function autoLayout(nodes: WFNode[]): WFNode[] {
  const COL_W = 240;
  const COL_H = 120;
  const COLS = 3;
  return nodes.map((node, idx) => ({
    ...node,
    position: {
      x: (idx % COLS) * COL_W + 100,
      y: Math.floor(idx / COLS) * COL_H + 100,
    },
  }));
}

// ─────────────────────────────────────────────────────────
// メインコンポーネント
// ─────────────────────────────────────────────────────────

export function WorkflowEditorPage() {
  const { workflowId: wfIdStr } = useParams({ strict: false }) as { workflowId: string };
  const workflowId = Number(wfIdStr);
  const toast = useToast();
  const queryClient = useQueryClient();

  // ─── データ取得 ───────────────────────────────────────
  const nodeTypesQuery = useQuery({
    queryKey: WORKFLOW_NODE_TYPES_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/workflows/node-types");
      if (error) throw new Error("ノードタイプ取得失敗");
      return (data as unknown as NodeTypeInfo[]) ?? [];
    },
    staleTime: Infinity,
  });

  const workflowQuery = useQuery({
    queryKey: workflowKey(workflowId),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/workflows/{workflow_id}", {
        params: { path: { workflow_id: workflowId } },
      });
      if (error) throw new Error("ワークフロー取得失敗");
      return data as WorkflowRead;
    },
  });

  const nodeTypeMap = useMemo(() => {
    const map = new Map<string, NodeTypeInfo>();
    for (const nt of nodeTypesQuery.data ?? []) map.set(nt.type, nt);
    return map;
  }, [nodeTypesQuery.data]);

  // ─── xyflow 状態 ─────────────────────────────────────
  const [nodes, setNodes, onNodesChange] = useNodesState<WFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const initialized = useRef(false);

  // workflowId が変わったら初期化フラグをリセットし、遷移先のワークフロー定義を
  // 取り込み直す（別ワークフローへ移動しても前の graph が残らないように）。
  // この effect は初期化 effect より前に宣言しているため同一コミットで先に走る。
  useEffect(() => {
    initialized.current = false;
  }, [workflowId]);

  useEffect(() => {
    if (!workflowQuery.data || !nodeTypesQuery.data || initialized.current) return;
    const def = workflowQuery.data.definition as {
      nodes: Record<string, unknown>[];
      edges: Record<string, unknown>[];
    };
    const initialNodes: WFNode[] = [];
    for (const bn of def.nodes ?? []) {
      const nt = nodeTypeMap.get(String(bn.type));
      if (!nt) continue;
      initialNodes.push(backendNodeToXyflow(bn, nt));
    }
    const initialEdges = (def.edges ?? []).map(backendEdgeToXyflow);
    setNodes(initialNodes);
    setEdges(initialEdges);
    initialized.current = true;
  }, [workflowQuery.data, nodeTypesQuery.data, nodeTypeMap, setNodes, setEdges]);

  // ─── エッジ接続ハンドラ ───────────────────────────────
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge(connection, eds));
    },
    [setEdges],
  );

  // ─── ノード選択 ──────────────────────────────────────
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = useMemo(
    () => (selectedNodeId ? (nodes as WFNode[]).find((n) => n.id === selectedNodeId) : null),
    [selectedNodeId, nodes],
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNodeId(node.id);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, []);

  // ─── パレット: ノード追加 ─────────────────────────────
  function addNode(nodeTypeInfo: NodeTypeInfo) {
    const defaultConfig: Record<string, unknown> = {};
    for (const field of nodeTypeInfo.config_schema) {
      if (!field.required && field.default !== undefined) {
        defaultConfig[field.key] = field.default;
      }
    }
    const newId = `${nodeTypeInfo.type}_${Date.now()}`;
    const existingCount = nodes.length;
    const newNode: WFNode = {
      id: newId,
      type: "workflowNode",
      position: {
        x: 100 + (existingCount % 4) * 240,
        y: 100 + Math.floor(existingCount / 4) * 140,
      },
      data: {
        nodeType: nodeTypeInfo.type,
        label: nodeTypeInfo.label,
        config: defaultConfig,
        configSchema: nodeTypeInfo.config_schema,
        outputHandles: nodeTypeInfo.output_handles,
        dynamicHandles: nodeTypeInfo.dynamic_handles,
      },
    };
    setNodes((nds) => [...(nds as WFNode[]), newNode]);
    setSelectedNodeId(newId);
  }

  // ─── 設定変更 ────────────────────────────────────────
  const onConfigChange = useCallback(
    (key: string, value: unknown) => {
      if (!selectedNodeId) return;
      setNodes((nds) =>
        (nds as WFNode[]).map((n) => {
          if (n.id !== selectedNodeId) return n;
          const updated: WFNode = {
            ...n,
            data: { ...n.data, config: { ...n.data.config, [key]: value } },
          };
          return updated;
        }),
      );
    },
    [selectedNodeId, setNodes],
  );

  // ─── ノード削除 ──────────────────────────────────────
  const deleteSelectedNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((nds) => (nds as WFNode[]).filter((n) => n.id !== selectedNodeId));
    setEdges((eds) =>
      (eds as Edge[]).filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId),
    );
    setSelectedNodeId(null);
  }, [selectedNodeId, setNodes, setEdges]);

  // ─── 保存 ────────────────────────────────────────────
  const wf = workflowQuery.data;

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!wf) throw new Error("ワークフローが未ロードです");
      const definition = {
        nodes: (nodes as WFNode[]).map(xyflowNodeToBackend),
        edges: (edges as Edge[]).map(xyflowEdgeToBackend),
      };
      const { data, error, response } = await api.PUT("/api/workflows/{workflow_id}", {
        params: { path: { workflow_id: workflowId } },
        body: {
          name: wf.name,
          number: wf.number,
          description: wf.description,
          enabled: wf.enabled,
          default_tts_provider_id: wf.default_tts_provider_id ?? undefined,
          definition,
        },
      });
      if (response.status === 422) {
        const detail = (error as { detail?: unknown } | undefined)?.detail;
        // FastAPI/pydantic の 422 detail は {msg,loc,...} の配列。人が読める msg を並べる。
        let msg: string;
        if (typeof detail === "string") {
          msg = detail;
        } else if (Array.isArray(detail)) {
          msg = detail
            .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
            .join(" / ");
        } else {
          msg = JSON.stringify(detail);
        }
        throw new Error(`定義が不正です: ${msg}`);
      }
      if (error || !data) throw new Error("保存に失敗しました");
      return data as WorkflowRead;
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(workflowKey(workflowId), saved);
      queryClient.invalidateQueries({ queryKey: WORKFLOWS_KEY });
      const warns = saved.warnings ?? [];
      if (warns.length > 0) {
        toast.warn(`保存しました（警告 ${warns.length} 件）`);
      } else {
        toast.success("ワークフローを保存しました");
      }
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  // ─── AI 生成 ─────────────────────────────────────────
  const [aiPrompt, setAiPrompt] = useState("");
  const [showAiInput, setShowAiInput] = useState(false);

  const generateMutation = useMutation({
    mutationFn: async (prompt: string) => {
      const { data, error } = await api.POST("/api/workflows/generate", {
        body: { prompt },
      });
      if (error) throw new Error("AI 生成に失敗しました");
      return data;
    },
    onSuccess: (result) => {
      if (!result) return;
      const def = result.definition as {
        nodes: Record<string, unknown>[];
        edges: Record<string, unknown>[];
      };
      const newNodes: WFNode[] = [];
      for (const bn of def.nodes ?? []) {
        const nt = nodeTypeMap.get(String(bn.type));
        if (!nt) continue;
        newNodes.push(backendNodeToXyflow(bn, nt));
      }
      const hasPositions = newNodes.some((n) => n.position.x !== 0 || n.position.y !== 0);
      const finalNodes = hasPositions ? newNodes : autoLayout(newNodes);
      setNodes(finalNodes);
      setEdges((def.edges ?? []).map(backendEdgeToXyflow));
      setShowAiInput(false);
      setAiPrompt("");
      const warnCount = (result.warnings ?? []).length;
      if (warnCount > 0) {
        toast.warn(`生成しました（警告 ${warnCount} 件）`);
      } else {
        toast.success("AI でフローを生成しました");
      }
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "AI 生成に失敗しました");
    },
  });

  // ─── カテゴリ別パレット ──────────────────────────────
  const nodeTypesByCategory = useMemo(() => {
    const map = new Map<string, NodeTypeInfo[]>();
    for (const nt of nodeTypesQuery.data ?? []) {
      const list = map.get(nt.category) ?? [];
      list.push(nt);
      map.set(nt.category, list);
    }
    return map;
  }, [nodeTypesQuery.data]);

  const savedWarnings = workflowQuery.data?.warnings ?? [];

  if (workflowQuery.isLoading || nodeTypesQuery.isLoading) {
    return (
      <div
        className={css({
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "text.muted",
        })}
      >
        読み込み中…
      </div>
    );
  }

  if (!wf) {
    return (
      <div className={css({ p: "6", color: "danger.text" })}>
        ワークフローが見つかりません
      </div>
    );
  }

  const typedSelectedNode = selectedNode as WFNode | null | undefined;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        margin: "-24px",
        // AppShell ヘッダ高さトークン（panda.config: sizes.header = 56px）と一致させる。
        height: "calc(100vh - 56px)",
        overflow: "hidden",
      }}
    >
      {/* ─── ツールバー ─── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "0 16px",
          height: 52,
          background: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
          flexShrink: 0,
          zIndex: 10,
        }}
      >
        <a
          href="/workflows"
          onClick={(e) => {
            e.preventDefault();
            window.history.back();
          }}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 13,
            color: "#64748b",
            textDecoration: "none",
          }}
        >
          <ArrowLeft size={16} />
          一覧へ
        </a>

        <div style={{ flex: 1, overflow: "hidden" }}>
          <span style={{ fontWeight: 600, fontSize: 14, color: "#1e293b" }}>{wf.name}</span>
          <span style={{ marginLeft: 8, fontSize: 13, color: "#64748b" }}>#{wf.number}</span>
          {savedWarnings.length > 0 && (
            <span
              title={savedWarnings.join("\n")}
              style={{
                marginLeft: 8,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: 12,
                color: "#d97706",
              }}
            >
              <AlertTriangle size={14} />
              警告 {savedWarnings.length} 件
            </span>
          )}
        </div>

        {showAiInput ? (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              style={{
                height: 36,
                padding: "0 12px",
                fontSize: 13,
                border: "1px solid #e2e8f0",
                borderRadius: 6,
                outline: "none",
                width: 280,
              }}
              placeholder="「受付後にメニューを表示するフロー」など"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && aiPrompt.trim())
                  generateMutation.mutate(aiPrompt.trim());
              }}
              autoFocus
            />
            <button
              type="button"
              className={button({ variant: "primary", size: "sm" })}
              disabled={!aiPrompt.trim() || generateMutation.isPending}
              onClick={() => generateMutation.mutate(aiPrompt.trim())}
            >
              {generateMutation.isPending ? "生成中…" : "生成"}
            </button>
            <button
              type="button"
              className={button({ variant: "ghost", size: "sm" })}
              onClick={() => setShowAiInput(false)}
            >
              <X size={16} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => setShowAiInput(true)}
          >
            <Sparkles size={16} />
            AI 生成
          </button>
        )}

        <button
          type="button"
          className={button({ variant: "primary" })}
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          style={{ height: 36, display: "inline-flex", alignItems: "center", gap: 6 }}
        >
          <Save size={16} />
          {saveMutation.isPending ? "保存中…" : "保存"}
        </button>
      </div>

      {/* ─── ボディ（パレット + キャンバス + 設定パネル）─── */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* ─── ノードパレット ─── */}
        <aside
          style={{
            width: 200,
            borderRight: "1px solid #e2e8f0",
            background: "#f8fafc",
            overflowY: "auto",
            flexShrink: 0,
          }}
        >
          <div
            style={{
              padding: "10px 12px 6px",
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.06em",
              textTransform: "uppercase",
              color: "#64748b",
              borderBottom: "1px solid #e2e8f0",
            }}
          >
            ノードパレット
          </div>
          {Array.from(nodeTypesByCategory.entries()).map(([cat, items]) => (
            <div key={cat}>
              <div
                style={{
                  padding: "8px 12px 4px",
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: "0.05em",
                  textTransform: "uppercase",
                  color: "#94a3b8",
                }}
              >
                {CATEGORY_LABEL[cat] ?? cat}
              </div>
              {items.map((nt) => (
                <PaletteButton key={nt.type} label={nt.label} onClick={() => addNode(nt)} />
              ))}
            </div>
          ))}
        </aside>

        {/* ─── xyflow キャンバス ─── */}
        <div style={{ flex: 1, position: "relative" }}>
          <ReactFlow
            nodes={nodes as WFNode[]}
            edges={edges as Edge[]}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            // macOS の delete キーは KeyboardEvent.key === "Backspace" のため両方許可する
            deleteKeyCode={["Backspace", "Delete"]}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.3}
            maxZoom={2}
          >
            <Background />
            <Controls />
            <MiniMap nodeStrokeWidth={3} />
          </ReactFlow>
        </div>

        {/* ─── 設定パネル ─── */}
        {typedSelectedNode && (
          <aside
            style={{
              width: 300,
              borderLeft: "1px solid #e2e8f0",
              background: "#ffffff",
              overflowY: "auto",
              flexShrink: 0,
              padding: 16,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <ConfigPanel
              nodeId={typedSelectedNode.id}
              nodeLabel={typedSelectedNode.data.label}
              config={typedSelectedNode.data.config}
              schema={typedSelectedNode.data.configSchema}
              onChange={onConfigChange}
              onDelete={deleteSelectedNode}
            />
          </aside>
        )}
      </div>
    </div>
  );
}

/** パレットの個別ボタン（ホバーを state で管理しないようインライン onMouseEnter で対応）。 */
function PaletteButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        display: "block",
        width: "100%",
        textAlign: "left",
        padding: "6px 12px",
        fontSize: 13,
        color: "#334155",
        background: "transparent",
        border: "none",
        cursor: "pointer",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "#e2e8f0";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.background = "transparent";
      }}
    >
      {label}
    </button>
  );
}
