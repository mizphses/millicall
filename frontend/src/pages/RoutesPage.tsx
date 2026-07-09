import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { NUMBER_PLAN_KEY, RING_GROUPS_KEY, EXTENSIONS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildUpsertPayload,
  emptyForm,
  formFromGroup,
  numberPlanKindLabel,
  validateForm,
  type NumberPlanEntryRead,
  type RingGroupFormValues,
  type RingGroupRead,
} from "./routes/formPayload";

type ExtensionRead = components["schemas"]["ExtensionRead"];

/** グループ番号の重複（409）を型で区別するためのエラー。フォームのインライン表示に使う。 */
class GroupNumberConflictError extends Error {}

async function fetchNumberPlan(): Promise<NumberPlanEntryRead[]> {
  const { data, error } = await api.GET("/api/number-plan");
  if (error) throw new Error("番号プランの取得に失敗しました");
  return data ?? [];
}

async function fetchRingGroups(): Promise<RingGroupRead[]> {
  const { data, error } = await api.GET("/api/ring-groups");
  if (error) throw new Error("グループ着信一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchExtensions(): Promise<ExtensionRead[]> {
  const { data, error } = await api.GET("/api/extensions");
  if (error) throw new Error("内線一覧の取得に失敗しました");
  return data ?? [];
}

async function createRingGroup(form: RingGroupFormValues): Promise<RingGroupRead> {
  const { data, error, response } = await api.POST("/api/ring-groups", {
    body: buildUpsertPayload(form),
  });
  if (response.status === 409) {
    throw new GroupNumberConflictError("この番号は既に使用されています");
  }
  if (error || !data) throw new Error("グループ着信の作成に失敗しました");
  return data;
}

async function updateRingGroup(
  id: number,
  form: RingGroupFormValues,
): Promise<RingGroupRead> {
  const { data, error, response } = await api.PATCH("/api/ring-groups/{group_id}", {
    params: { path: { group_id: id } },
    body: buildUpsertPayload(form),
  });
  if (response.status === 409) {
    throw new GroupNumberConflictError("この番号は既に使用されています");
  }
  if (error || !data) throw new Error("グループ着信の更新に失敗しました");
  return data;
}

async function deleteRingGroup(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/ring-groups/{group_id}", {
    params: { path: { group_id: id } },
  });
  if (error) throw new Error("グループ着信の削除に失敗しました");
}

/** kind ごとのバッジ色。ラベルは numberPlanKindLabel と対で使う。 */
function kindBadgeTone(kind: string): "accent" | "success" | "warn" | "neutral" {
  switch (kind) {
    case "extension":
      return "accent";
    case "ai_agent":
      return "success";
    case "workflow":
      return "warn";
    default:
      // ring_group ほか
      return "neutral";
  }
}

export function RoutesPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const planQuery = useQuery({ queryKey: NUMBER_PLAN_KEY, queryFn: fetchNumberPlan });
  const groupsQuery = useQuery({ queryKey: RING_GROUPS_KEY, queryFn: fetchRingGroups });
  const extensionsQuery = useQuery({ queryKey: EXTENSIONS_KEY, queryFn: fetchExtensions });

  const extensions = extensionsQuery.data ?? [];

  // 内線 id → Read（グループのメンバー表示用）。
  const extensionMap = useMemo(() => {
    const map = new Map<number, ExtensionRead>();
    for (const ext of extensions) map.set(ext.id, ext);
    return map;
  }, [extensions]);

  const [editing, setEditing] = useState<RingGroupRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<RingGroupFormValues>(emptyForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof RingGroupFormValues, string>>
  >({});

  const [deleteTarget, setDeleteTarget] = useState<RingGroupRead | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(group: RingGroupRead) {
    setEditing(group);
    setForm(formFromGroup(group));
    setFieldErrors({});
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  /** メンバー内線チェックボックスのトグル。 */
  function toggleMember(extensionId: number, checked: boolean) {
    setForm((f) => ({
      ...f,
      member_extension_ids: checked
        ? [...f.member_extension_ids, extensionId]
        : f.member_extension_ids.filter((id) => id !== extensionId),
    }));
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateRingGroup(editing.id, form);
      return createRingGroup(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RING_GROUPS_KEY });
      queryClient.invalidateQueries({ queryKey: NUMBER_PLAN_KEY });
      toast.success(editing ? "グループ着信を更新しました" : "グループ着信を作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof GroupNumberConflictError) {
        setFieldErrors((prev) => ({ ...prev, number: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteRingGroup(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RING_GROUPS_KEY });
      queryClient.invalidateQueries({ queryKey: NUMBER_PLAN_KEY });
      toast.success("グループ着信を削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateForm(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    saveMutation.mutate();
  }

  const planColumns: Column<NumberPlanEntryRead>[] = [
    { key: "number", header: "番号", width: "110px" },
    {
      key: "kind",
      header: "種別",
      width: "130px",
      render: (row) => (
        <span className={badge({ tone: kindBadgeTone(row.kind) })}>
          {numberPlanKindLabel(row.kind)}
        </span>
      ),
    },
    { key: "label", header: "名前" },
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
      key: "inbound_trunks",
      header: "着信トランク",
      render: (row) => (row.inbound_trunks.length > 0 ? row.inbound_trunks.join(", ") : "—"),
    },
  ];

  const groupColumns: Column<RingGroupRead>[] = [
    { key: "number", header: "番号", width: "110px" },
    { key: "name", header: "名前" },
    {
      key: "member_extension_ids",
      header: "メンバー内線",
      render: (row) =>
        row.member_extension_ids.length > 0
          ? row.member_extension_ids
              .map((id) => extensionMap.get(id)?.number ?? `ID:${id}`)
              .join(", ")
          : "—",
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
            <Pencil size={14} />編集
          </button>
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            onClick={() => setDeleteTarget(row)}
          >
            <Trash2 size={14} />削除
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="ルーティング"
      description="統一内線番号プランの一覧と、グループ着信（複数内線の一斉鳴動）の管理"
    >
      <div className={css({ display: "flex", flexDirection: "column", gap: "8" })}>
        <section>
          <SectionHeading
            title="番号プラン"
            description="内線・AI エージェント・ワークフロー・グループが共有する番号空間"
          />
          <DataTable
            columns={planColumns}
            rows={planQuery.data ?? []}
            rowKey={(row) => `${row.kind}-${row.id}`}
            loading={planQuery.isLoading}
            emptyMessage="番号がまだありません。内線・AI エージェント・グループ着信を登録すると表示されます。"
          />
        </section>

        <section>
          <div
            className={css({
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: "4",
            })}
          >
            <SectionHeading
              title="グループ着信"
              description="1 つの番号で複数の内線を一斉に鳴らすグループ"
            />
            <button
              type="button"
              className={button({ variant: "primary" })}
              onClick={openCreate}
            >
              グループを追加
            </button>
          </div>
          <DataTable
            columns={groupColumns}
            rows={groupsQuery.data ?? []}
            rowKey={(row) => row.id}
            loading={groupsQuery.isLoading}
            emptyMessage="グループ着信がまだありません。「グループを追加」から登録してください。"
          />
        </section>
      </div>

      <SlidePanel
        open={panelOpen}
        title={editing ? "グループを編集" : "グループを追加"}
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
              form="ring-group-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="ring-group-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field label="番号（2〜6 桁）" error={fieldErrors.number}>
            <input
              className={input({ invalid: fieldErrors.number ? true : undefined })}
              value={form.number}
              onChange={(e) => setForm((f) => ({ ...f, number: e.target.value }))}
              placeholder="200"
              autoFocus={editing === null}
            />
          </Field>

          <Field label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="営業部"
              maxLength={100}
            />
          </Field>

          {/* チェックボックス自体が label を持つため、Field（label ラップ方式）は使わない */}
          <div>
            <span
              className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
            >
              メンバー内線
            </span>
            <div
              className={css({
                display: "flex",
                flexDirection: "column",
                gap: "1",
                maxH: "240px",
                overflowY: "auto",
                borderWidth: "1px",
                borderStyle: "solid",
                borderColor: "border",
                borderRadius: "md",
                p: "2",
              })}
            >
              {extensions.length === 0 ? (
                <p className={css({ fontSize: "sm", color: "text.muted", m: 0 })}>
                  内線がまだありません。先に内線を登録してください。
                </p>
              ) : (
                extensions.map((ext) => (
                  <label
                    key={ext.id}
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
                      checked={form.member_extension_ids.includes(ext.id)}
                      onChange={(e) => toggleMember(ext.id, e.target.checked)}
                    />
                    {ext.number} — {ext.display_name}
                  </label>
                ))
              )}
            </div>
          </div>

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
        title="グループ着信を削除"
        message={
          deleteTarget
            ? `グループ「${deleteTarget.name}」（${deleteTarget.number}）を削除します。この操作は取り消せません。`
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

/** ページ内セクションの小見出し（番号プラン / グループ着信）。 */
function SectionHeading({ title, description }: { title: string; description?: string }) {
  return (
    <div className={css({ mb: "3" })}>
      <h2 className={css({ fontSize: "lg", fontWeight: "600", color: "text" })}>{title}</h2>
      {description ? (
        <p className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>{description}</p>
      ) : null}
    </div>
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
