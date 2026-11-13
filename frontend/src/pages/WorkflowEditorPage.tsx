import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type NodeTypes,
  type ReactFlowInstance,
} from "@xyflow/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  useBlocker,
  useLocation,
  useNavigate,
  useParams,
} from "@tanstack/react-router";
import {
  ArrowLeft,
  Plus,
  Save,
  Settings,
  Sparkles,
  Undo2,
  X,
} from "lucide-react";
import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Field } from "../components/Field";
import { SlidePanel } from "../components/SlidePanel";
import {
  PROVIDERS_KEY,
  WORKFLOWS_KEY,
  WORKFLOW_NODE_TYPES_KEY,
  workflowKey,
} from "../queryKeys";
import { useDirtyState } from "../hooks/useDirtyState";
import { useToast } from "../toast/ToastProvider";
import { ConfigPanel } from "./workflows/ConfigPanel";
import { WorkflowNodeComponent } from "./workflows/WorkflowNode";
import type { NodeTypeInfo, ProviderOption } from "./workflows/types";
import {
  CATEGORY_LABEL,
  connectEdge,
  editorSnapshot,
  isValidWorkflowConnection,
  placeNewNode,
  readDefinition,
  validateWorkflowMetadata,
  writeDefinition,
  type ConfigDrafts,
  type WFNode,
  type WorkflowGraph,
  type WorkflowMetadata,
} from "./workflows/editorState";

type WorkflowRead = components["schemas"]["WorkflowRead"];
const NODE_TYPES: NodeTypes = {
  workflowNode: WorkflowNodeComponent as unknown as NodeTypes[string],
};
const emptyMetadata: WorkflowMetadata = {
  name: "",
  number: "",
  description: "",
  enabled: true,
  default_tts_provider_id: null,
};

export function WorkflowEditorPage() {
  const { workflowId: wfIdStr } = useParams({ strict: false }) as {
    workflowId: string;
  };
  const workflowId = Number(wfIdStr);
  const toast = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const selectedNodeId = new URLSearchParams(location.searchStr).get("node");
  const flowRef = useRef<ReactFlowInstance<WFNode, Edge> | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const initializedId = useRef<number | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<WFNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [metadata, setMetadata] = useState<WorkflowMetadata>(emptyMetadata);
  const [baseline, setBaseline] = useState("");
  const [initializationError, setInitializationError] = useState("");
  const [drafts, setDrafts] = useState<ConfigDrafts>({});
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(Boolean(selectedNodeId));
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [metadataDraft, setMetadataDraft] =
    useState<WorkflowMetadata>(emptyMetadata);
  const [metadataErrors, setMetadataErrors] = useState<
    Partial<Record<"name" | "number", string>>
  >({});
  const [aiOpen, setAiOpen] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [preview, setPreview] = useState<
    (WorkflowGraph & { warnings: string[] }) | null
  >(null);
  const [undo, setUndo] = useState<
    (WorkflowGraph & { drafts: ConfigDrafts; applied: string }) | null
  >(null);
  const [undoConfirm, setUndoConfirm] = useState(false);
  const [saveError, setSaveError] = useState("");

  const nodeTypesQuery = useQuery({
    queryKey: WORKFLOW_NODE_TYPES_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/workflows/node-types");
      if (error || !data?.length)
        throw new Error(
          "ノード一覧を読み込めませんでした。読み込みが完了するまで保存できません。",
        );
      return data as unknown as NodeTypeInfo[];
    },
    staleTime: Infinity,
  });
  const workflowQuery = useQuery({
    queryKey: workflowKey(workflowId),
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/workflows/{workflow_id}",
        { params: { path: { workflow_id: workflowId } } },
      );
      if (error || !data)
        throw new Error(
          response.status === 404
            ? "ワークフローが見つかりません。"
            : "ワークフローを読み込めませんでした。",
        );
      return data;
    },
  });
  const providersQuery = useQuery({
    queryKey: PROVIDERS_KEY,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/providers");
      if (error) throw new Error("プロバイダーを読み込めませんでした。");
      return (data ?? []) as ProviderOption[];
    },
    enabled: metadataOpen,
  });

  useEffect(() => {
    if (
      initializedId.current === workflowId ||
      !workflowQuery.data ||
      !nodeTypesQuery.data
    )
      return;
    setInitializationError("");
    try {
      const graph = readDefinition(
        workflowQuery.data.definition,
        nodeTypesQuery.data,
      );
      const next: WorkflowMetadata = {
        name: workflowQuery.data.name,
        number: workflowQuery.data.number,
        description: workflowQuery.data.description ?? "",
        enabled: workflowQuery.data.enabled,
        default_tts_provider_id:
          workflowQuery.data.default_tts_provider_id ?? null,
      };
      setNodes(graph.nodes);
      setEdges(graph.edges);
      setMetadata(next);
      setDrafts({});
      setUndo(null);
      setSaveError("");
      setBaseline(editorSnapshot(next, graph));
      initializedId.current = workflowId;
    } catch (error) {
      setInitializationError(
        error instanceof Error ? error.message : "定義を読み込めませんでした。",
      );
    }
  }, [workflowId, workflowQuery.data, nodeTypesQuery.data, setNodes, setEdges]);

  const invalidDrafts = Object.entries(drafts).filter(
    ([, draft]) => draft.error,
  );
  const currentSnapshot = editorSnapshot(metadata, { nodes, edges });
  const dirty =
    initializedId.current === workflowId &&
    (currentSnapshot !== baseline || invalidDrafts.length > 0);
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const selectNode = useCallback(
    (id: string | null, open = true) => {
      void navigate({
        to: "/workflows/$workflowId",
        params: { workflowId: String(workflowId) },
        search: { node: id ?? undefined } as never,
        replace: true,
      });
      setSettingsOpen(Boolean(id) && open);
      setPaletteOpen(false);
    },
    [navigate, workflowId],
  );

  function focusNode(id: string) {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        void flowRef.current?.fitView({
          nodes: [{ id }],
          padding: 0.8,
          maxZoom: 1,
          duration: 200,
        });
      });
    });
  }
  function addNode(info: NodeTypeInfo) {
    const rect = canvasRef.current?.getBoundingClientRect();
    const position =
      rect && flowRef.current
        ? flowRef.current.screenToFlowPosition({
            x: rect.left + rect.width / 2 - 90,
            y: rect.top + rect.height / 2 - 60,
          })
        : { x: 100, y: 100 };
    const config = Object.fromEntries(
      info.config_schema
        .filter((field) => field.default !== undefined)
        .map((field) => [field.key, field.default]),
    );
    const graph = readDefinition(
      {
        nodes: [
          {
            id: `${info.type}_${crypto.randomUUID()}`,
            type: info.type,
            config,
            position: placeNewNode(position, nodes),
          },
        ],
        edges: [],
      },
      [info],
    );
    const node = graph.nodes[0];
    setNodes((previous) => [...previous, node]);
    selectNode(node.id, false);
    setSettingsOpen(false);
    focusNode(node.id);
    toast.success(
      `${info.label}を追加しました。「ノード設定」から編集できます`,
    );
  }
  function deleteSelectedNode() {
    if (!selectedNodeId) return;
    setNodes((previous) =>
      previous.filter((node) => node.id !== selectedNodeId),
    );
    setEdges((previous) =>
      previous.filter(
        (edge) =>
          edge.source !== selectedNodeId && edge.target !== selectedNodeId,
      ),
    );
    setDrafts((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(
          ([key]) => !key.startsWith(`${selectedNodeId}:`),
        ),
      ),
    );
    selectNode(null);
  }
  function updateConfig(key: string, value: unknown) {
    setNodes((previous) =>
      previous.map((node) =>
        node.id === selectedNodeId
          ? {
              ...node,
              data: {
                ...node.data,
                config: { ...node.data.config, [key]: value },
              },
            }
          : node,
      ),
    );
  }

  const saveMutation = useMutation({
    mutationFn: async (snapshot: string) => {
      if (
        !nodeTypesQuery.data ||
        initializationError ||
        initializedId.current !== workflowId ||
        invalidDrafts.length
      )
        throw new Error("未完了の入力または読み込みエラーを確認してください。");
      const body = JSON.parse(snapshot) as WorkflowMetadata & {
        definition: ReturnType<typeof writeDefinition>;
      };
      const { data, error, response } = await api.PUT(
        "/api/workflows/{workflow_id}",
        { params: { path: { workflow_id: workflowId } }, body },
      );
      if (response.status === 409)
        throw new Error(
          "この内線番号は既に使用されています。「基本情報」で別の番号に変更してください。",
        );
      if (response.status === 422) {
        const detail = (error as { detail?: unknown })?.detail;
        const description =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail
                  .map((item: unknown) =>
                    typeof item === "string"
                      ? item
                      : String((item as { msg?: unknown })?.msg ?? ""),
                  )
                  .filter(Boolean)
                  .join(" / ")
              : "ノードの設定と接続を確認してください。";
        throw new Error(`保存できませんでした: ${description}`);
      }
      if (error || !data)
        throw new Error(
          "保存に失敗しました。変更内容は保持されています。再試行してください。",
        );
      return data as WorkflowRead;
    },
    onSuccess: (saved, snapshot) => {
      setBaseline(snapshot);
      setSaveError("");
      setUndo(null);
      queryClient.setQueryData(workflowKey(workflowId), saved);
      void queryClient.invalidateQueries({ queryKey: WORKFLOWS_KEY });
      toast.success("ワークフローを保存しました");
    },
    onError: (error) => {
      setSaveError(error.message);
      toast.error(error.message);
    },
  });

  useDirtyState(dirty, saveMutation.isPending);
  const blocker = useBlocker({
    shouldBlockFn: ({ current, next }) =>
      (dirty || saveMutation.isPending) && current.pathname !== next.pathname,
    enableBeforeUnload: dirty || saveMutation.isPending,
    withResolver: true,
  });

  const generateMutation = useMutation({
    mutationFn: async (prompt: string) => {
      const { data, error } = await api.POST("/api/workflows/generate", {
        body: { prompt },
      });
      if (error || !data)
        throw new Error(
          "AI 生成に失敗しました。現在のフローは保持されています。",
        );
      return {
        ...readDefinition(data.definition, nodeTypesQuery.data ?? [], true),
        warnings: data.warnings ?? [],
      };
    },
    onSuccess: (result) => setPreview(result),
    onError: (error) => toast.error(error.message),
  });
  function applyPreview() {
    if (!preview) return;
    setUndo({
      nodes,
      edges,
      drafts,
      applied: editorSnapshot(metadata, preview),
    });
    setNodes(preview.nodes);
    setEdges(preview.edges);
    setDrafts({});
    setAiOpen(false);
    setPreview(null);
    setAiPrompt("");
    selectNode(null);
    requestAnimationFrame(() => {
      void flowRef.current?.fitView({ padding: 0.2, duration: 200 });
    });
    toast.success("生成案を適用しました。保存するまで元に戻せます");
  }
  function restoreBeforeAI() {
    if (!undo) return;
    setNodes(undo.nodes);
    setEdges(undo.edges);
    setDrafts(undo.drafts);
    setUndo(null);
    setUndoConfirm(false);
    selectNode(null);
    requestAnimationFrame(() => {
      void flowRef.current?.fitView({ padding: 0.2 });
    });
  }

  const categories = useMemo(() => {
    const result = new Map<string, NodeTypeInfo[]>();
    for (const info of nodeTypesQuery.data ?? [])
      result.set(info.category, [...(result.get(info.category) ?? []), info]);
    return result;
  }, [nodeTypesQuery.data]);
  const loadError =
    workflowQuery.error?.message ||
    nodeTypesQuery.error?.message ||
    initializationError;
  if (loadError)
    return (
      <section className={css({ display: "grid", gap: "4" })}>
        <h1 className={css({ fontSize: "xl", fontWeight: "600" })}>
          ワークフロー
        </h1>
        <p role="alert" className={css({ color: "danger.text" })}>
          {loadError}
        </p>
        <div className={css({ display: "flex", gap: "2", flexWrap: "wrap" })}>
          <button
            type="button"
            className={button({ variant: "primary" })}
            onClick={() => {
              void workflowQuery.refetch();
              void nodeTypesQuery.refetch();
            }}
          >
            再試行
          </button>
          <Link to="/workflows" className={button({ variant: "secondary" })}>
            一覧へ戻る
          </Link>
        </div>
      </section>
    );
  if (
    !workflowQuery.data ||
    !nodeTypesQuery.data ||
    initializedId.current !== workflowId
  )
    return <p role="status">ワークフローを読み込み中…</p>;

  return (
    <div
      className={css({
        display: "flex",
        flexDirection: "column",
        gap: "3",
        minWidth: 0,
      })}
    >
      <header
        className={css({
          display: "flex",
          gap: "3",
          flexWrap: "wrap",
          alignItems: "center",
        })}
      >
        <Link
          to="/workflows"
          className={button({ variant: "ghost", size: "sm" })}
        >
          <ArrowLeft size={16} />
          一覧へ
        </Link>
        <div className={css({ flex: "1 1 200px", minWidth: 0 })}>
          <h1
            className={css({
              fontSize: "xl",
              fontWeight: "600",
              overflowWrap: "anywhere",
            })}
          >
            {metadata.name}
          </h1>
          <p className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>
            内線番号 {metadata.number} · {metadata.enabled ? "有効" : "無効"}
          </p>
        </div>
        <span
          role="status"
          className={badge({ tone: dirty ? "warn" : "neutral" })}
        >
          {dirty ? "未保存の変更" : "保存済み"}
        </span>
        <button
          type="button"
          className={button({ variant: "primary" })}
          disabled={
            !dirty || saveMutation.isPending || invalidDrafts.length > 0
          }
          onClick={() => saveMutation.mutate(currentSnapshot)}
        >
          <Save size={16} />
          {saveMutation.isPending ? "保存中…" : "保存"}
        </button>
      </header>
      <div
        role="toolbar"
        aria-label="ワークフローの編集"
        className={css({ display: "flex", flexWrap: "wrap", gap: "2" })}
      >
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          aria-expanded={paletteOpen}
          aria-controls="workflow-palette"
          onClick={() => {
            setPaletteOpen(!paletteOpen);
            setSettingsOpen(false);
          }}
        >
          <Plus size={16} />
          ノードを追加
        </button>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          onClick={() => {
            setMetadataDraft({ ...metadata });
            setMetadataErrors({});
            setMetadataOpen(true);
          }}
        >
          <Settings size={16} />
          基本情報
        </button>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          onClick={() => setAiOpen(true)}
        >
          <Sparkles size={16} />
          AI で生成
        </button>
        {undo && (
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() =>
              currentSnapshot !== undo.applied || invalidDrafts.length
                ? setUndoConfirm(true)
                : restoreBeforeAI()
            }
          >
            <Undo2 size={16} />
            生成前に戻す
          </button>
        )}
        {selectedNode && (
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            aria-expanded={settingsOpen}
            aria-controls="workflow-node-settings"
            onClick={() => {
              setSettingsOpen(!settingsOpen);
              setPaletteOpen(false);
            }}
          >
            ノード設定
          </button>
        )}
      </div>
      {saveError && (
        <p
          role="alert"
          className={css({ color: "danger.text", fontSize: "sm" })}
        >
          {saveError}
        </p>
      )}
      {invalidDrafts.length > 0 && (
        <p
          role="alert"
          className={css({ color: "danger.text", fontSize: "sm" })}
        >
          保存するにはノード設定の入力エラー {invalidDrafts.length}{" "}
          件を修正してください。
          {invalidDrafts.map(([key]) => (
            <button
              key={key}
              type="button"
              className={css({ textDecoration: "underline", ml: "2" })}
              onClick={() => selectNode(key.slice(0, key.lastIndexOf(":")))}
            >
              該当ノードを開く
            </button>
          ))}
        </p>
      )}
      {(workflowQuery.data.warnings ?? []).length > 0 && (
        <details className={css({ color: "warn.text", fontSize: "sm" })}>
          <summary className={css({ cursor: "pointer" })}>
            保存済みの警告 {workflowQuery.data.warnings?.length} 件
          </summary>
          <ul className={css({ pl: "5", listStyleType: "disc" })}>
            {workflowQuery.data.warnings?.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
      <div
        className={css({
          display: "flex",
          position: "relative",
          height: "max(420px, calc(100dvh - 260px))",
          minWidth: 0,
          borderWidth: "1px",
          borderColor: "border",
          borderRadius: "lg",
          overflow: "hidden",
          bg: "white",
        })}
      >
        {paletteOpen && (
          <aside
            id="workflow-palette"
            aria-label="追加するノード"
            className={css({
              width: "220px",
              maxWidth: "100%",
              flexShrink: 0,
              bg: "gray.50",
              borderRightWidth: "1px",
              borderColor: "border",
              overflowY: "auto",
              zIndex: 10,
              "@media (max-width: 1100px)": {
                position: "absolute",
                inset: "0 auto 0 0",
                width: "min(300px, 100%)",
              },
            })}
          >
            <div
              className={css({
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                p: "3",
              })}
            >
              <h2 className={css({ fontWeight: "600" })}>ノードを追加</h2>
              <button
                type="button"
                aria-label="ノード一覧を閉じる"
                className={button({ variant: "ghost", size: "sm" })}
                onClick={() => setPaletteOpen(false)}
              >
                <X size={16} />
              </button>
            </div>
            {Array.from(categories).map(([category, items]) => (
              <section key={category} className={css({ px: "3", pb: "4" })}>
                <h3
                  className={css({
                    color: "text.muted",
                    fontWeight: "600",
                    fontSize: "sm",
                    mb: "2",
                  })}
                >
                  {CATEGORY_LABEL[category] ?? category}
                </h3>
                <div className={css({ display: "grid", gap: "1" })}>
                  {items.map((info) => (
                    <button
                      key={info.type}
                      type="button"
                      className={button({ variant: "secondary", size: "sm" })}
                      onClick={() => addNode(info)}
                    >
                      {info.label}
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </aside>
        )}
        <div
          ref={canvasRef}
          onKeyDownCapture={(event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            const target = event.target as HTMLElement;
            if (!target.classList.contains("react-flow__node")) return;
            const id = target.dataset.id;
            if (id) {
              event.preventDefault();
              event.stopPropagation();
              selectNode(id);
            }
          }}
          className={css({ flex: 1, minWidth: 0, position: "relative" })}
        >
          <ReactFlow<WFNode, Edge>
            nodes={nodes.map((node) => ({
              ...node,
              selected: node.id === selectedNodeId,
              data: { ...node.data, onOpen: () => selectNode(node.id) },
            }))}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onInit={(instance) => {
              flowRef.current = instance;
            }}
            onNodesChange={(changes) => {
              onNodesChange(changes);
              const removedIds = changes
                .filter((change) => change.type === "remove")
                .map((change) => change.id);
              if (removedIds.length) {
                setDrafts((previous) =>
                  Object.fromEntries(
                    Object.entries(previous).filter(
                      ([key]) =>
                        !removedIds.some((id) => key.startsWith(`${id}:`)),
                    ),
                  ),
                );
                if (selectedNodeId && removedIds.includes(selectedNodeId))
                  selectNode(null);
              }
              const selection = changes.find(
                (change) => change.type === "select" && change.selected,
              );
              if (selection?.type === "select") selectNode(selection.id);
            }}
            onEdgesChange={onEdgesChange}
            onConnect={(connection) =>
              setEdges((previous) => connectEdge(previous, connection))
            }
            isValidConnection={isValidWorkflowConnection}
            onNodeClick={(_, node) => selectNode(node.id)}
            onPaneClick={() => selectNode(null)}
            deleteKeyCode={["Backspace", "Delete"]}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
            minZoom={0.2}
            maxZoom={2}
          >
            <Background />
            <Controls />
            <MiniMap
              className={css({
                "@media (max-width: 600px)": { display: "none" },
              })}
            />
          </ReactFlow>
        </div>
        {settingsOpen && selectedNode && (
          <aside
            id="workflow-node-settings"
            aria-label="ノード設定"
            className={css({
              width: "320px",
              maxWidth: "100%",
              minWidth: 0,
              borderLeftWidth: "1px",
              borderColor: "border",
              bg: "white",
              flexShrink: 0,
              p: "4",
              overflowY: "auto",
              zIndex: 10,
              "@media (max-width: 1100px)": {
                position: "absolute",
                inset: "0 0 0 auto",
                width: "min(360px, 100%)",
              },
            })}
          >
            <div
              className={css({
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                mb: "3",
              })}
            >
              <h2 className={css({ fontWeight: "600" })}>ノード設定</h2>
              <button
                type="button"
                aria-label="ノード設定を閉じる"
                className={button({ variant: "ghost", size: "sm" })}
                onClick={() => setSettingsOpen(false)}
              >
                <X size={16} />
              </button>
            </div>
            <ConfigPanel
              nodeId={selectedNode.id}
              nodeLabel={selectedNode.data.label}
              nodeType={selectedNode.data.nodeType}
              config={selectedNode.data.config}
              schema={selectedNode.data.configSchema}
              onChange={updateConfig}
              onDelete={deleteSelectedNode}
              currentWorkflowId={workflowId}
              drafts={drafts}
              onDraftChange={(key, draft) =>
                setDrafts((previous) => ({ ...previous, [key]: draft }))
              }
            />
          </aside>
        )}
      </div>
      <label
        className={css({
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "2",
          fontSize: "sm",
          color: "text.muted",
        })}
      >
        ノードを選んで設定
        <select
          className={input()}
          style={{ width: "auto", maxWidth: "100%" }}
          value={selectedNodeId ?? ""}
          onChange={(event) => {
            const id = event.target.value;
            selectNode(id || null);
            if (id) focusNode(id);
          }}
        >
          <option value="">ノードを選択</option>
          {nodes.map((node, index) => (
            <option key={node.id} value={node.id}>
              {index + 1}. {node.data.label}
            </option>
          ))}
        </select>
      </label>
      <SlidePanel
        open={metadataOpen}
        title="ワークフローの基本情報"
        dirty={JSON.stringify(metadataDraft) !== JSON.stringify(metadata)}
        onClose={() => setMetadataOpen(false)}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={() => setMetadataOpen(false)}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="workflow-metadata"
              className={button({ variant: "primary" })}
            >
              変更を反映
            </button>
          </>
        }
      >
        <form
          id="workflow-metadata"
          className={css({ display: "grid", gap: "4" })}
          onSubmit={(event) => {
            event.preventDefault();
            const errors = validateWorkflowMetadata(metadataDraft);
            setMetadataErrors(errors);
            if (Object.keys(errors).length) return;
            setMetadata({
              ...metadataDraft,
              name: metadataDraft.name.trim(),
              number: metadataDraft.number.trim(),
            });
            setMetadataOpen(false);
          }}
        >
          <p className={css({ color: "text.muted", fontSize: "sm" })}>
            変更を反映したあと、エディタの「保存」で確定します。
          </p>
          <Field label="名前" required error={metadataErrors.name}>
            <input
              className={input()}
              value={metadataDraft.name}
              maxLength={100}
              onChange={(event) =>
                setMetadataDraft({ ...metadataDraft, name: event.target.value })
              }
            />
          </Field>
          <Field
            label="内線番号"
            required
            error={metadataErrors.number}
            help="2〜6桁の数字"
          >
            <input
              className={input()}
              inputMode="numeric"
              maxLength={6}
              value={metadataDraft.number}
              onChange={(event) =>
                setMetadataDraft({
                  ...metadataDraft,
                  number: event.target.value,
                })
              }
            />
          </Field>
          <Field label="説明">
            <textarea
              className={input()}
              rows={3}
              value={metadataDraft.description}
              onChange={(event) =>
                setMetadataDraft({
                  ...metadataDraft,
                  description: event.target.value,
                })
              }
            />
          </Field>
          <label
            className={css({ display: "flex", gap: "2", alignItems: "center" })}
          >
            <input
              type="checkbox"
              checked={metadataDraft.enabled}
              onChange={(event) =>
                setMetadataDraft({
                  ...metadataDraft,
                  enabled: event.target.checked,
                })
              }
            />
            このワークフローを有効にする
          </label>
          <Field label="既定の音声合成プロバイダー">
            <select
              className={input()}
              disabled={providersQuery.isLoading || providersQuery.isError}
              value={metadataDraft.default_tts_provider_id ?? ""}
              onChange={(event) =>
                setMetadataDraft({
                  ...metadataDraft,
                  default_tts_provider_id: event.target.value
                    ? Number(event.target.value)
                    : null,
                })
              }
            >
              <option value="">指定なし</option>
              {providersQuery.data
                ?.filter((provider) => provider.type === "tts")
                .map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name}
                  </option>
                ))}
            </select>
          </Field>
          {providersQuery.isError && (
            <p role="alert">
              {providersQuery.error.message}{" "}
              <button
                type="button"
                onClick={() => void providersQuery.refetch()}
                className={css({ textDecoration: "underline" })}
              >
                再試行
              </button>
            </p>
          )}
        </form>
      </SlidePanel>
      <SlidePanel
        open={aiOpen}
        title="AI でフローを生成"
        dirty={Boolean(aiPrompt || preview)}
        busy={generateMutation.isPending}
        onClose={() => {
          setAiOpen(false);
          setAiPrompt("");
          setPreview(null);
        }}
        footer={
          preview ? (
            <>
              <button
                type="button"
                className={button({ variant: "secondary" })}
                onClick={() => setPreview(null)}
              >
                指示を修正
              </button>
              <button
                type="button"
                className={button({ variant: "primary" })}
                onClick={applyPreview}
              >
                この生成案に置き換える
              </button>
            </>
          ) : (
            <button
              type="submit"
              form="workflow-generate"
              disabled={!aiPrompt.trim() || generateMutation.isPending}
              className={button({ variant: "primary" })}
            >
              {generateMutation.isPending ? "生成中…" : "生成案を確認"}
            </button>
          )
        }
      >
        {preview ? (
          <div className={css({ display: "grid", gap: "4" })}>
            <p>
              現在の {nodes.length} ノード・{edges.length} 接続を、生成案の{" "}
              {preview.nodes.length} ノード・{preview.edges.length}{" "}
              接続に置き換えます。現在の未保存の編集も置き換わります。適用後、保存するまでは「生成前に戻す」で復元できます。
            </p>
            <div aria-label="生成案のプレビュー" style={{ height: 320 }}>
              <ReactFlow
                nodes={preview.nodes}
                edges={preview.edges}
                nodeTypes={NODE_TYPES}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={false}
                minZoom={0.1}
                maxZoom={1}
                fitView
                fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
              >
                <Background />
                <Controls showInteractive={false} />
              </ReactFlow>
            </div>
            <ol
              className={css({
                pl: "5",
                listStyleType: "decimal",
                fontSize: "sm",
              })}
            >
              {preview.nodes.map((node) => (
                <li key={node.id}>{node.data.label}</li>
              ))}
            </ol>
            {preview.warnings.length > 0 && (
              <div role="status" className={css({ color: "warn.text" })}>
                <h3>生成案の警告</h3>
                <ul>
                  {preview.warnings.map((warning, index) => (
                    <li key={index}>{warning}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : (
          <form
            id="workflow-generate"
            className={css({ display: "grid", gap: "4" })}
            onSubmit={(event) => {
              event.preventDefault();
              if (aiPrompt.trim() && !generateMutation.isPending)
                generateMutation.mutate(aiPrompt.trim());
            }}
          >
            <p className={css({ color: "text.muted", fontSize: "sm" })}>
              生成案を確認してから、現在のフローに適用します。
            </p>
            <Field label="作りたい応対フロー" required>
              <textarea
                className={input()}
                rows={6}
                value={aiPrompt}
                disabled={generateMutation.isPending}
                placeholder="例: 受付後にメニューを案内し、問い合わせの種類で担当者につなぐ"
                onChange={(event) => setAiPrompt(event.target.value)}
              />
            </Field>
            {generateMutation.isError && (
              <p role="alert" className={css({ color: "danger.text" })}>
                {generateMutation.error.message}
              </p>
            )}
          </form>
        )}
      </SlidePanel>
      <ConfirmDialog
        open={blocker.status === "blocked"}
        title="未保存の変更を破棄しますか？"
        message="フローや基本情報に未保存の変更があります。編集を続ける場合はキャンセルしてください。"
        confirmLabel="破棄して移動"
        destructive
        busy={saveMutation.isPending}
        onConfirm={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      />
      <ConfirmDialog
        open={undoConfirm}
        title="生成前のフローに戻しますか？"
        message="生成案の適用後に行ったノードの編集も元に戻ります。"
        confirmLabel="生成前に戻す"
        destructive
        onConfirm={restoreBeforeAI}
        onCancel={() => setUndoConfirm(false)}
      />
    </div>
  );
}
