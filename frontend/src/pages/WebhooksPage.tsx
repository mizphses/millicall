import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { History, Pencil, Send, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button, input } from "styled-system/recipes";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { Field } from "../components/Field";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { WEBHOOKS_KEY, webhookDeliveriesKey } from "../queryKeys";
import { useToast } from "../toast/ToastProvider";
import { formatDateTime } from "./cdr/format";
import {
  WEBHOOK_EVENT_OPTIONS,
  buildCreatePayload,
  buildUpdatePayload,
  deliveryStatus,
  emptyWebhookForm,
  formFromWebhook,
  toggleEvent,
  validateWebhookForm,
  webhookEventLabel,
  type WebhookDeliveryRead,
  type WebhookFormValues,
  type WebhookRead,
  type WebhookTestResult,
} from "./webhooks/formPayload";

async function fetchWebhooks(): Promise<WebhookRead[]> {
  const { data, error } = await api.GET("/api/webhooks");
  if (error) throw new Error("Webhook 一覧の取得に失敗しました");
  return data ?? [];
}

async function fetchDeliveries(id: number): Promise<WebhookDeliveryRead[]> {
  const { data, error } = await api.GET("/api/webhooks/{webhook_id}/deliveries", {
    params: { path: { webhook_id: id }, query: { limit: 100 } },
  });
  if (error) throw new Error("配信ログの取得に失敗しました");
  return data ?? [];
}

async function saveWebhook(editing: WebhookRead | null, form: WebhookFormValues): Promise<WebhookRead> {
  if (editing) {
    const { data, error } = await api.PATCH("/api/webhooks/{webhook_id}", {
      params: { path: { webhook_id: editing.id } },
      body: buildUpdatePayload(form, editing),
    });
    if (error || !data) throw new Error("Webhook の更新に失敗しました（URL を確認してください）");
    return data;
  }
  const { data, error } = await api.POST("/api/webhooks", { body: buildCreatePayload(form) });
  if (error || !data) throw new Error("Webhook の作成に失敗しました（URL を確認してください）");
  return data;
}

async function sendTest(id: number): Promise<WebhookTestResult> {
  const { data, error } = await api.POST("/api/webhooks/{webhook_id}/test", {
    params: { path: { webhook_id: id } },
  });
  if (error || !data) throw new Error("テスト送信を実行できませんでした");
  return data;
}

async function deleteWebhook(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/webhooks/{webhook_id}", {
    params: { path: { webhook_id: id } },
  });
  if (error) throw new Error("Webhook の削除に失敗しました");
}

const checkboxRow = css({
  display: "flex",
  alignItems: "flex-start",
  gap: "2",
  fontSize: "md",
  cursor: "pointer",
});

export function WebhooksPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const listQuery = useQuery({ queryKey: WEBHOOKS_KEY, queryFn: fetchWebhooks });

  const [panelOpen, setPanelOpen] = useState(false);
  const [editing, setEditing] = useState<WebhookRead | null>(null);
  const [form, setForm] = useState<WebhookFormValues>(emptyWebhookForm());
  const [fieldErrors, setFieldErrors] = useState<
    Partial<Record<keyof WebhookFormValues, string>>
  >({});
  const [deleteTarget, setDeleteTarget] = useState<WebhookRead | null>(null);
  const [logTarget, setLogTarget] = useState<WebhookRead | null>(null);

  const deliveriesQuery = useQuery({
    queryKey: webhookDeliveriesKey(logTarget?.id ?? 0),
    queryFn: () => fetchDeliveries(logTarget?.id ?? 0),
    enabled: logTarget !== null,
  });

  function openCreate() {
    setEditing(null);
    setForm(emptyWebhookForm());
    setFieldErrors({});
    setPanelOpen(true);
  }

  function openEdit(hook: WebhookRead) {
    setEditing(hook);
    setForm(formFromWebhook(hook));
    setFieldErrors({});
    setPanelOpen(true);
  }

  const saveMutation = useMutation({
    mutationFn: () => saveWebhook(editing, form),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WEBHOOKS_KEY });
      toast.success(editing ? "Webhook を更新しました" : "Webhook を作成しました");
      setPanelOpen(false);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "保存に失敗しました"),
  });

  const testMutation = useMutation({
    mutationFn: (hook: WebhookRead) => sendTest(hook.id),
    onSuccess: (result, hook) => {
      queryClient.invalidateQueries({ queryKey: webhookDeliveriesKey(hook.id) });
      if (result.success) toast.success(`テスト送信に成功しました（HTTP ${result.status_code}）`);
      else toast.error(`テスト送信に失敗しました: ${result.error ?? "不明なエラー"}`);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "テスト送信に失敗しました"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteWebhook(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WEBHOOKS_KEY });
      toast.success("Webhook を削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "削除に失敗しました"),
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errors = validateWebhookForm(form);
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    saveMutation.mutate();
  }

  const columns: Column<WebhookRead>[] = [
    { key: "name", header: "名前", width: "160px" },
    {
      key: "url",
      header: "URL",
      render: (row) => (
        <span className={css({ fontFamily: "mono", fontSize: "sm", wordBreak: "break-all" })}>
          {row.url}
        </span>
      ),
    },
    {
      key: "events",
      header: "イベント",
      render: (row) =>
        row.events.length
          ? row.events.map((e) => (
              <span key={e} className={css({ mr: "1" })}>
                <span className={badge({ tone: "accent" })}>{webhookEventLabel(e)}</span>
              </span>
            ))
          : "—",
    },
    {
      key: "has_secret",
      header: "署名",
      width: "80px",
      render: (row) =>
        row.has_secret ? (
          <span className={badge({ tone: "success" })}>あり</span>
        ) : (
          <span className={badge({ tone: "warn" })}>なし</span>
        ),
    },
    {
      key: "enabled",
      header: "状態",
      width: "80px",
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
      width: "330px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end", flexWrap: "wrap" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => testMutation.mutate(row)}
            disabled={testMutation.isPending}
          >
            <Send size={14} />テスト
          </button>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => setLogTarget(row)}
          >
            <History size={14} />配信ログ
          </button>
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

  const deliveryColumns: Column<WebhookDeliveryRead>[] = [
    {
      key: "created_at",
      header: "日時",
      width: "160px",
      render: (row) => formatDateTime(row.created_at),
    },
    { key: "event", header: "イベント", render: (row) => webhookEventLabel(row.event) },
    { key: "attempt", header: "試行", width: "60px", align: "right" },
    {
      key: "success",
      header: "結果",
      render: (row) => {
        const s = deliveryStatus(row);
        return (
          <span title={row.error ?? undefined} className={badge({ tone: s.tone })}>
            {s.label}
          </span>
        );
      },
    },
    {
      key: "error",
      header: "エラー",
      render: (row) => (
        <span className={css({ fontSize: "sm", color: "text.muted", wordBreak: "break-all" })}>
          {row.error ?? ""}
        </span>
      ),
    },
  ];

  return (
    <PageLayout
      title="Webhook"
      description="着信・通話終了・FAX 受信を外部システムへ HTTP POST で通知します。"
      actions={
        <button type="button" className={button({ variant: "primary" })} onClick={openCreate}>
          Webhook を追加
        </button>
      }
    >
      <DataTable
        columns={columns}
        rows={listQuery.data ?? []}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        error={listQuery.error}
        onRetry={() => void listQuery.refetch()}
        emptyMessage="Webhook がまだありません。「Webhook を追加」から登録してください。"
      />

      <SlidePanel
        busy={saveMutation.isPending}
        open={panelOpen}
        title={editing ? "Webhook を編集" : "Webhook を追加"}
        onClose={() => setPanelOpen(false)}
        footer={
          <>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              onClick={() => setPanelOpen(false)}
              disabled={saveMutation.isPending}
            >
              キャンセル
            </button>
            <button
              type="submit"
              form="webhook-form"
              className={button({ variant: "primary" })}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? "保存中…" : "保存"}
            </button>
          </>
        }
      >
        <form
          id="webhook-form"
          onSubmit={handleSubmit}
          className={css({ display: "flex", flexDirection: "column", gap: "4" })}
        >
          <Field required label="名前" error={fieldErrors.name}>
            <input
              className={input({ invalid: fieldErrors.name ? true : undefined })}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="CRM 連携"
              maxLength={100}
              autoFocus={editing === null}
            />
          </Field>
          <Field required label="送信先 URL" error={fieldErrors.url}>
            <input
              className={input({ invalid: fieldErrors.url ? true : undefined })}
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="https://example.com/millicall/webhook"
              inputMode="url"
            />
          </Field>
          <Field
            label={editing ? "署名シークレット（変更する場合のみ入力）" : "署名シークレット（推奨）"}
            error={fieldErrors.secret}
          >
            <input
              className={input({ invalid: fieldErrors.secret ? true : undefined })}
              type="password"
              autoComplete="new-password"
              value={form.secret}
              disabled={form.clear_secret}
              onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value }))}
              placeholder={editing?.has_secret ? "設定済み（空欄なら変更しません）" : "受信側で署名検証に使う共有鍵"}
            />
          </Field>
          {editing?.has_secret && (
            <label className={checkboxRow}>
              <input
                type="checkbox"
                checked={form.clear_secret}
                onChange={(e) => setForm((f) => ({ ...f, clear_secret: e.target.checked, secret: "" }))}
              />
              シークレットを削除する（署名なしで送信）
            </label>
          )}

          <fieldset className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
            <legend className={css({ fontSize: "sm", color: "text.muted", mb: "1" })}>
              通知するイベント
            </legend>
            {WEBHOOK_EVENT_OPTIONS.map((opt) => (
              <label key={opt.value} className={checkboxRow}>
                <input
                  type="checkbox"
                  checked={form.events.includes(opt.value)}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, events: toggleEvent(f.events, opt.value, e.target.checked) }))
                  }
                />
                <span>
                  {opt.label}
                  <span className={css({ color: "text.muted", fontSize: "sm", ml: "2" })}>
                    {opt.value} — {opt.help}
                  </span>
                </span>
              </label>
            ))}
            {fieldErrors.events && (
              <p role="alert" className={css({ color: "danger.text", fontSize: "sm" })}>
                {fieldErrors.events}
              </p>
            )}
          </fieldset>

          <label className={checkboxRow}>
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            有効にする
          </label>
        </form>
      </SlidePanel>

      <SlidePanel
        open={logTarget !== null}
        title={logTarget ? `配信ログ — ${logTarget.name}` : "配信ログ"}
        onClose={() => setLogTarget(null)}
      >
        <p className={css({ fontSize: "sm", color: "text.muted", mb: "3" })}>
          直近 100 件（保存は Webhook ごとに 200 件まで）。失敗時は 1 秒・5 秒後に再試行します。
        </p>
        <DataTable
          columns={deliveryColumns}
          rows={deliveriesQuery.data ?? []}
          rowKey={(row) => row.id}
          loading={deliveriesQuery.isLoading}
          error={deliveriesQuery.error}
          onRetry={() => void deliveriesQuery.refetch()}
          emptyMessage="まだ配信していません。"
        />
      </SlidePanel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Webhook を削除"
        message={
          deleteTarget
            ? `Webhook「${deleteTarget.name}」を削除します。配信ログも削除されます。`
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
