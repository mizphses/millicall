import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PlugZap, Pencil, Trash2 } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { PROVIDERS_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useToast } from "../toast/ToastProvider";
import {
  buildCreatePayload,
  buildUpdatePayload,
  emptyForm,
  formFromProvider,
  hasErrors,
  KIND_CATALOG,
  KIND_ORDER,
  TYPE_LABEL,
  validateForm,
  withKind,
  type ProviderFormErrors,
  type ProviderFormValues,
  type ProviderKind,
  type ProviderRead,
} from "./providers/formPayload";

/** プロバイダ名の重複（409）を型で区別するためのエラー。 */
class ProviderNameConflictError extends Error {}

/** 接続テストの結果（ProviderTestResult）。 */
interface TestOutcome {
  ok: boolean;
  detail: string;
  latency_ms: number;
}

async function fetchProviders(): Promise<ProviderRead[]> {
  const { data, error } = await api.GET("/api/providers");
  if (error) throw new Error("プロバイダ一覧の取得に失敗しました");
  return data ?? [];
}

async function createProvider(form: ProviderFormValues): Promise<ProviderRead> {
  const { data, error, response } = await api.POST("/api/providers", {
    body: buildCreatePayload(form),
  });
  if (response.status === 409) {
    throw new ProviderNameConflictError("このプロバイダ名は既に使用されています");
  }
  if (error || !data) throw new Error("プロバイダの作成に失敗しました");
  return data;
}

async function updateProvider(
  id: number,
  form: ProviderFormValues,
  original: ProviderRead,
): Promise<ProviderRead> {
  const { data, error, response } = await api.PATCH("/api/providers/{provider_id}", {
    params: { path: { provider_id: id } },
    body: buildUpdatePayload(form, original),
  });
  if (response.status === 409) {
    throw new ProviderNameConflictError("このプロバイダ名は既に使用されています");
  }
  if (error || !data) throw new Error("プロバイダの更新に失敗しました");
  return data;
}

async function deleteProvider(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/providers/{provider_id}", {
    params: { path: { provider_id: id } },
  });
  if (error) throw new Error("プロバイダの削除に失敗しました");
}

async function runTest(id: number): Promise<TestOutcome> {
  const { data, error } = await api.POST("/api/providers/{provider_id}/test", {
    params: { path: { provider_id: id } },
  });
  if (error || !data) throw new Error("接続テストの実行に失敗しました");
  return data;
}

export function ProvidersPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const listQuery = useQuery({ queryKey: PROVIDERS_KEY, queryFn: fetchProviders });

  const [editing, setEditing] = useState<ProviderRead | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [form, setForm] = useState<ProviderFormValues>(emptyForm());
  const [errors, setErrors] = useState<ProviderFormErrors>({ config: {} });
  const [showApiKey, setShowApiKey] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<ProviderRead | null>(null);

  // 接続テスト: プロバイダ id → 結果（インライン表示用）。
  const [testResults, setTestResults] = useState<Record<number, TestOutcome>>({});
  const [testingId, setTestingId] = useState<number | null>(null);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm());
    setErrors({ config: {} });
    setShowApiKey(false);
    setPanelOpen(true);
  }

  function openEdit(provider: ProviderRead) {
    setEditing(provider);
    setForm(formFromProvider(provider));
    setErrors({ config: {} });
    setShowApiKey(false);
    setPanelOpen(true);
  }

  function closePanel() {
    setPanelOpen(false);
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (editing) return updateProvider(editing.id, form, editing);
      return createProvider(form);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PROVIDERS_KEY });
      toast.success(editing ? "プロバイダを更新しました" : "プロバイダを作成しました");
      setPanelOpen(false);
    },
    onError: (err) => {
      if (err instanceof ProviderNameConflictError) {
        setErrors((prev) => ({ ...prev, name: err.message }));
        return;
      }
      toast.error(err instanceof Error ? err.message : "保存に失敗しました");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteProvider(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PROVIDERS_KEY });
      toast.success("プロバイダを削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "削除に失敗しました");
    },
  });

  const testMutation = useMutation({
    mutationFn: (id: number) => runTest(id),
    onMutate: (id) => setTestingId(id),
    onSuccess: (result, id) => {
      setTestResults((prev) => ({ ...prev, [id]: result }));
    },
    onError: (err, id) => {
      setTestResults((prev) => ({
        ...prev,
        [id]: {
          ok: false,
          detail: err instanceof Error ? err.message : "接続テストに失敗しました",
          latency_ms: 0,
        },
      }));
    },
    onSettled: () => setTestingId(null),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const nextErrors = validateForm(form, editing ? "edit" : "create");
    setErrors(nextErrors);
    if (hasErrors(nextErrors)) return;
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

  const providers = listQuery.data ?? [];
  const activeKind = KIND_CATALOG[form.kind];

  return (
    <PageLayout
      title="プロバイダ"
      description="LLM / TTS / STT プロバイダの登録と接続テスト"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          プロバイダを追加
        </button>
      }
    >
      {listQuery.isLoading ? (
        <p className={css({ color: "text.muted", py: "6" })}>読み込み中…</p>
      ) : providers.length === 0 ? (
        <div
          className={cx(
            panel(),
            css({ p: "8", textAlign: "center", color: "text.muted" }),
          )}
        >
          プロバイダがまだありません。右上の「プロバイダを追加」から登録してください。
        </div>
      ) : (
        <div
          className={css({
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(token(sizes.providerCard), 1fr))",
            gap: "4",
          })}
        >
          {providers.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              onEdit={() => openEdit(p)}
              onDelete={() => setDeleteTarget(p)}
              onTest={() => testMutation.mutate(p.id)}
              testing={testingId === p.id}
              result={testResults[p.id]}
            />
          ))}
        </div>
      )}

      <SlidePanel
        open={panelOpen}
        title={editing ? "プロバイダを編集" : "プロバイダを追加"}
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
              form="provider-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="provider-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          {editing === null ? (
            <div>
              <span
                className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "2" })}
              >
                種別を選択
              </span>
              <div
                className={css({
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(token(sizes.kindCard), 1fr))",
                  gap: "2",
                })}
              >
                {KIND_ORDER.map((kind) => {
                  const def = KIND_CATALOG[kind];
                  const selected = form.kind === kind;
                  return (
                    <button
                      type="button"
                      key={kind}
                      onClick={() => setForm((f) => withKind(f, kind))}
                      className={cx(
                        panel(),
                        css({
                          p: "3",
                          textAlign: "left",
                          cursor: "pointer",
                          borderColor: selected ? "accent" : "border",
                          bg: selected ? "accent.soft" : "white",
                          _hover: { borderColor: "accent" },
                        }),
                      )}
                    >
                      <span className={css({ display: "flex", alignItems: "center", gap: "2" })}>
                        <span className={css({ fontWeight: "600", fontSize: "md" })}>
                          {def.label}
                        </span>
                        <span className={badge({ tone: "neutral" })}>{TYPE_LABEL[def.type]}</span>
                      </span>
                      <span
                        className={css({ display: "block", fontSize: "sm", color: "text.muted", mt: "1" })}
                      >
                        {def.description}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
              <span className={css({ fontWeight: "600", fontSize: "md" })}>{activeKind.label}</span>
              <span className={badge({ tone: "accent" })}>{TYPE_LABEL[activeKind.type]}</span>
              <span className={css({ fontSize: "sm", color: "text.subtle" })}>種別は変更できません</span>
            </div>
          )}

          <Field label="名前（識別子）" error={errors.name}>
            <input
              className={input({ invalid: errors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="my-gpt4o"
              autoFocus={editing === null}
            />
          </Field>

          {activeKind.usesApiKey ? (
            <div>
              <div className={css({ display: "flex", alignItems: "flex-end", gap: "2" })}>
                <label className={css({ display: "block", flex: "1" })}>
                  <span
                    className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
                  >
                    {editing ? "API キー（空のまま＝変更しない）" : "API キー（任意）"}
                  </span>
                  <input
                    className={cx(input(), css({ width: "100%" }))}
                    type={showApiKey ? "text" : "password"}
                    value={form.api_key}
                    onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                    placeholder={
                      editing
                        ? editing.api_key_masked
                          ? `現在: ${editing.api_key_masked}`
                          : "変更する場合のみ入力"
                        : "sk-..."
                    }
                    autoComplete="new-password"
                  />
                </label>
                <button
                  type="button"
                  className={button({ variant: "secondary", size: "sm" })}
                  onClick={() => setShowApiKey((v) => !v)}
                >
                  {showApiKey ? "隠す" : "表示"}
                </button>
              </div>
            </div>
          ) : null}

          {activeKind.fields.map((field) => (
            <Field key={field.key} label={field.label} error={errors.config[field.key]}>
              <input
                className={input({ invalid: errors.config[field.key] ? true : undefined })}
                inputMode={field.valueType === "number" ? "decimal" : undefined}
                value={form.config[field.key] ?? ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    config: { ...f.config, [field.key]: e.target.value },
                  }))
                }
                placeholder={field.placeholder}
              />
            </Field>
          ))}

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
        title="プロバイダを削除"
        message={
          deleteTarget
            ? `プロバイダ「${deleteTarget.name}」を削除します。この操作は取り消せません。`
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

/** 一覧の 1 プロバイダを表すカード（Dify ライク）。 */
function ProviderCard({
  provider,
  onEdit,
  onDelete,
  onTest,
  testing,
  result,
}: {
  provider: ProviderRead;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
  testing: boolean;
  result?: TestOutcome;
}) {
  const def = KIND_CATALOG[provider.kind as ProviderKind] as (typeof KIND_CATALOG)[ProviderKind] | undefined;
  const kindLabel = def ? def.label : provider.kind;
  const typeLabel = TYPE_LABEL[provider.type as keyof typeof TYPE_LABEL] ?? provider.type;

  return (
    <div className={cx(panel(), css({ p: "4", display: "flex", flexDirection: "column", gap: "3" }))}>
      <div className={css({ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "2" })}>
        <div className={css({ minWidth: "0" })}>
          <div
            className={css({ fontWeight: "600", fontSize: "md", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" })}
          >
            {provider.name}
          </div>
          <div className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>{kindLabel}</div>
        </div>
        {provider.enabled ? (
          <span className={badge({ tone: "success" })}>有効</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>無効</span>
        )}
      </div>

      <div className={css({ display: "flex", flexWrap: "wrap", gap: "2", alignItems: "center" })}>
        <span className={badge({ tone: "accent" })}>{typeLabel}</span>
        <span className={css({ fontSize: "sm", color: "text.subtle" })}>
          {provider.api_key_masked ? `キー: ${provider.api_key_masked}` : "キー未設定"}
        </span>
      </div>

      {result ? (
        <div className={css({ display: "flex", flexDirection: "column", gap: "1" })}>
          <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
            {result.ok ? (
              <span className={badge({ tone: "success" })}>接続成功</span>
            ) : (
              <span className={badge({ tone: "danger" })}>接続失敗</span>
            )}
            <span className={css({ fontSize: "sm", color: "text.muted" })}>{result.latency_ms} ms</span>
          </div>
          {result.detail ? (
            <p
              className={css({
                fontSize: "sm",
                color: result.ok ? "text.muted" : "danger.text",
                wordBreak: "break-word",
              })}
            >
              {result.detail}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className={css({ display: "flex", gap: "2", mt: "auto", pt: "1" })}>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          onClick={onTest}
          disabled={testing}
        >
          <PlugZap size={14} />{testing ? "テスト中…" : "接続テスト"}
        </button>
        <span className={css({ flex: "1" })} />
        <button type="button" className={button({ variant: "secondary", size: "sm" })} onClick={onEdit}>
          <Pencil size={14} />編集
        </button>
        <button type="button" className={button({ variant: "ghost", size: "sm" })} onClick={onDelete}>
          <Trash2 size={14} />削除
        </button>
      </div>
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
