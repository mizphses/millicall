import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromRoute,
  validateForm,
  type RouteFormValues,
  type RouteRead,
} from "./routes/formPayload";

type ExtensionRead = components["schemas"]["ExtensionRead"];
type AiAgentRead = components["schemas"]["AiAgentRead"];

/** TanStack Query キー。 */
const ROUTES_KEY = ["routes"] as const;
const EXTENSIONS_KEY = ["extensions"] as const;
const AI_AGENTS_KEY = ["ai-agents"] as const;

/** マッチ番号の重複（409）を型で区別するためのエラー。 */
class MatchNumberConflictError extends Error {}

async function fetchRoutes(): Promise<RouteRead[]> {
  const { data, error } = await api.GET("/api/routes");
  if (error) throw new Error("ルーティング一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchExtensions(): Promise<ExtensionRead[]> {
  const { data, error } = await api.GET("/api/extensions");
  if (error) throw new Error("内線一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchAiAgents(): Promise<AiAgentRead[]> {
  const { data, error } = await api.GET("/api/ai-agents");
  if (error) throw new Error("AI エージェント一覧の取得に失敗しました");
  return data ?? [];
}

async function createRoute(form: RouteFormValues): Promise<RouteRead> {
  const { data, error, response } = await api.POST("/api/routes", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) {
    throw new MatchNumberConflictError("このマッチ番号は既に使用されています");
  }
  if (error || !data) throw new Error("ルーティングの作成に失敗しました");
  return data;
}

async function updateRoute(
  id: number,
  form: RouteFormValues,
  original: RouteRead,
): Promise<RouteRead> {
  const { data, error } = await api.PATCH("/api/routes/{route_id}", {
    params: { path: { route_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (error || !data) throw new Error("ルーティングの更新に失敗しました");
  return data;
}

async function deleteRoute(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/routes/{route_id}", {
    params: { path: { route_id: id } },
  });
  if (error) throw new Error("ルーティングの削除に失敗しました");
}

/** target_type + target_value を人間可読な文字列に変換する。 */
function formatTarget(
  route: RouteRead,
  extensions: ExtensionRead[],
  aiAgents: AiAgentRead[],
): string {
  if (route.target_type === "extension") {
    const ext = extensions.find((e) => e.number === route.target_value);
    return ext
      ? `内線 ${ext.number}（${ext.display_name}）`
      : `内線 ${route.target_value}`;
  }
  if (route.target_type === "ai_agent") {
    const agentId = parseInt(route.target_value, 10);
    const agent = aiAgents.find((a) => a.id === agentId);
    return agent ? `AI: ${agent.name}` : `AI エージェント #${route.target_value}`;
  }
  return route.target_value;
}

export function RoutesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: ROUTES_KEY, queryFn: fetchRoutes });
  const extensionsQuery = useQuery({ queryKey: EXTENSIONS_KEY, queryFn: fetchExtensions });
  const aiAgentsQuery = useQuery({ queryKey: AI_AGENTS_KEY, queryFn: fetchAiAgents });

  const extensions = extensionsQuery.data ?? [];
  const aiAgents = aiAgentsQuery.data ?? [];

  const [editing, setEditing] = useState<RouteRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<RouteFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<keyof RouteFormValues, string>>>(
    {},
  );

  const [deleteTarget, setDeleteTarget] = useState<RouteRead | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(route: RouteRead) {
    setEditing(route);
    setForm(formFromRoute(route));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  /** target_type が変わったら target_value をリセットする。 */
  function handleTargetTypeChange(newType: RouteFormValues["target_type"]) {
    setForm((f) => ({ ...f, target_type: newType, target_value: "" }));
    setFieldErrors((prev) => ({ ...prev, target_value: undefined }));
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateRoute(editing.id, form, editing);
      return createRoute(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ROUTES_KEY });
      toast.success(editing ? "ルーティングを更新しました" : "ルーティングを作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof MatchNumberConflictError) {
        setFieldErrors((prev) => ({ ...prev, match_number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteRoute(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ROUTES_KEY });
      toast.success("ルーティングを削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateForm(form, editing ? "edit" : "create");
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    if (editing) {
      const payload = buildUpdatePayload(form, editing);
      if (Object.keys(payload).length === 0) {
        toast.show("変更はありません", "neutral");
        setPanelOpen(false);
        return;
      }
    }
    saveMutation.mutate();
  }

  const columns: Column<RouteRead>[] = [
    { key: "match_number", header: "マッチ番号", width: "140px" },
    {
      key: "target_type",
      header: "種別",
      width: "120px",
      render: (row) =>
        row.target_type === "extension" ? (
          <span className={badge({ tone: "accent" })}>内線</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>AI エージェント</span>
        ),
    },
    {
      key: "target_value",
      header: "転送先",
      render: (row) => formatTarget(row, extensions, aiAgents),
    },
    {
      key: "enabled",
      header: "状態",
      width: "90px",
      render: (row) =>
        row.enabled ? (
          <span className={badge({ tone: "success" })}>有効</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>無効</span>
        ),
    },
    {
      key: "actions",
      header: "操作",
      width: "160px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => openEdit(row)}
          >
            編集
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => setDeleteTarget(row)}
          >
            削除
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="ルーティング"
      description="着信番号と転送先（内線・AI エージェント）の対応を管理"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          ルールを追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        emptyMessage="ルーティングルールがまだありません。右上の「ルールを追加」から登録してください。"
      />

      <SlidePanel
        open={panelOpen}
        title={editing ? "ルールを編集" : "ルールを追加"}
        onClose={closePanel}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={closePanel}
              disabled={saveMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="route-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="route-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="マッチ番号" error={fieldErrors.match_number}>
            <input
              className={input({ invalid: fieldErrors.match_number ? true : undefined })}
              value={form.match_number}
              onChange={(e) => setForm((f) => ({ ...f, match_number: e.target.value }))}
              disabled={editing !== null}
              placeholder="0312345678"
              autoFocus={editing === null}
            />
          </Field>

          <Field label="転送先の種別" error={fieldErrors.target_type}>
            <select
              className={input()}
              value={form.target_type}
              onChange={(e) =>
                handleTargetTypeChange(
                  e.target.value as RouteFormValues["target_type"],
                )
              }
            >
              <option value="extension">内線</option>
              <option value="ai_agent">AI エージェント</option>
            </select>
          </Field>

          {form.target_type === "extension" ? (
            <Field label="内線" error={fieldErrors.target_value}>
              <select
                className={input({ invalid: fieldErrors.target_value ? true : undefined })}
                value={form.target_value}
                onChange={(e) => setForm((f) => ({ ...f, target_value: e.target.value }))}
              >
                <option value="">内線を選択してください</option>
                {extensions.map((ext) => (
                  <option key={ext.number} value={ext.number}>
                    {ext.number} — {ext.display_name}
                  </option>
                ))}
              </select>
            </Field>
          ) : (
            <Field label="AI エージェント" error={fieldErrors.target_value}>
              <select
                className={input({ invalid: fieldErrors.target_value ? true : undefined })}
                value={form.target_value}
                onChange={(e) => setForm((f) => ({ ...f, target_value: e.target.value }))}
              >
                <option value="">AI エージェントを選択してください</option>
                {aiAgents.map((agent) => (
                  <option key={agent.id} value={String(agent.id)}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </Field>
          )}

          <label
            className={css({
              display: "flex",
              alignItems: "center",
              gap: "2",
              fontSize: "md",
              cursor: "pointer",
            })}
          >
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            有効にする
          </label>
        </form>
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="ルーティングルールを削除"
        message={
          deleteTarget
            ? `マッチ番号「${deleteTarget.match_number}」のルーティングルールを削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </PageLayout>
  );
}

/** フォーム 1 項目（ラベル + 入力 + インラインエラー）。
 * wrap 方式: label > span（テキスト）+ children（input 等）で関連付ける。
 * Task 2（ExtensionsPage）で確立した標準形を踏襲。 */
function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className={css({ display: "block" })}>
        <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
          {label}
        </span>
        {children}
      </label>
      {error ? (
        <p className={css({ color: "danger.text", fontSize: "sm", mt: "1" })}>{error}</p>
      ) : null}
    </div>
  );
}
