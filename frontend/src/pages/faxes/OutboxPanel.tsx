import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Download, FileUp, RotateCcw, Send, X } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, input, panel } from "styled-system/recipes";

import { api } from "../../api/client";
import type { components } from "../../api/schema";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { DataTable, type Column } from "../../components/DataTable";
import { Field } from "../../components/Field";
import { FAX_OUTBOX_KEY, TRUNKS_KEY, faxOutboxKey } from "../../queryKeys";
import { useToast } from "../../toast/ToastProvider";
import { formatDateTime } from "../cdr/format";
import { totalPages } from "./format";
import {
  apiErrorMessage,
  buildFaxSendFormData,
  canCancel,
  canRetry,
  emptyFaxSendForm,
  hasActiveJobs,
  outboxPdfUrl,
  outboxStatus,
  validateFaxSendForm,
  type FaxOutboxRead,
  type FaxSendErrors,
  type FaxSendFormValues,
} from "./outbox";

type TrunkRead = components["schemas"]["TrunkRead"];

const PAGE_SIZE = 50;
const REFRESH_MS = 5000;

async function fetchOutbox(offset: number): Promise<{ items: FaxOutboxRead[]; total: number }> {
  const { data, error } = await api.GET("/api/faxes/outbox", {
    params: { query: { limit: PAGE_SIZE, offset } },
  });
  if (error || !data) throw new Error("送信ボックスの取得に失敗しました");
  return data;
}

async function fetchTrunks(): Promise<TrunkRead[]> {
  const { data, error } = await api.GET("/api/trunks");
  if (error) throw new Error("トランク一覧の取得に失敗しました");
  return data ?? [];
}

async function sendFax(form: FaxSendFormValues): Promise<FaxOutboxRead> {
  const formData = buildFaxSendFormData(form);
  const { data, error } = await api.POST("/api/faxes/outbox", {
    // multipart はブラウザに境界付きの Content-Type を付けさせる。
    body: {} as never,
    bodySerializer: () => formData,
  });
  if (error || !data) throw new Error(apiErrorMessage(error, "FAX の送信登録に失敗しました"));
  return data;
}

async function retryJob(id: number): Promise<FaxOutboxRead> {
  const { data, error } = await api.POST("/api/faxes/outbox/{job_id}/retry", {
    params: { path: { job_id: id } },
  });
  if (error || !data) throw new Error(apiErrorMessage(error, "再送に失敗しました"));
  return data;
}

async function cancelJob(id: number): Promise<FaxOutboxRead> {
  const { data, error } = await api.POST("/api/faxes/outbox/{job_id}/cancel", {
    params: { path: { job_id: id } },
  });
  if (error || !data) throw new Error(apiErrorMessage(error, "取り消しに失敗しました"));
  return data;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** FAX 送信タブ: 送信フォーム（宛先・トランク・PDF）と送信ボックス一覧。 */
export function OutboxPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<FaxSendFormValues>(emptyFaxSendForm);
  const [errors, setErrors] = useState<FaxSendErrors>({});
  const [dragging, setDragging] = useState(false);
  const [page, setPage] = useState(1);
  const [cancelTarget, setCancelTarget] = useState<FaxOutboxRead | null>(null);
  const offset = (page - 1) * PAGE_SIZE;

  const query = useQuery({
    queryKey: faxOutboxKey(PAGE_SIZE, offset),
    queryFn: () => fetchOutbox(offset),
    // 送信待ち・送信中があれば状態の変化を自動で反映する。
    refetchInterval: (q) => (hasActiveJobs(q.state.data?.items ?? []) ? REFRESH_MS : false),
  });
  const trunksQuery = useQuery({ queryKey: TRUNKS_KEY, queryFn: fetchTrunks });
  const enabledTrunks = (trunksQuery.data ?? [])
    .filter((t) => t.enabled)
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  const defaultTrunk = enabledTrunks[0];

  const invalidate = () => queryClient.invalidateQueries({ queryKey: FAX_OUTBOX_KEY });

  const sendMutation = useMutation({
    mutationFn: sendFax,
    onSuccess: (job) => {
      invalidate();
      toast.success(`${job.to_number} への FAX（${job.pages} ページ）を送信キューに登録しました`);
      setForm(emptyFaxSendForm());
      setErrors({});
      if (fileInput.current) fileInput.current.value = "";
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "送信登録に失敗しました"),
  });
  const retryMutation = useMutation({
    mutationFn: retryJob,
    onSuccess: () => {
      invalidate();
      toast.success("再送を登録しました");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "再送に失敗しました"),
  });
  const cancelMutation = useMutation({
    mutationFn: cancelJob,
    onSuccess: () => {
      invalidate();
      toast.success("送信を取り消しました");
      setCancelTarget(null);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "取り消しに失敗しました"),
  });

  const setFile = (file: File | null) => {
    setForm((f) => ({ ...f, file }));
    setErrors((e) => ({ ...e, file: undefined }));
  };

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) setFile(file);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateFaxSendForm(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    sendMutation.mutate(form);
  };

  const total = query.data?.total ?? 0;
  const pages = totalPages(total, PAGE_SIZE);

  const columns: Column<FaxOutboxRead>[] = [
    {
      key: "created_at",
      header: "登録日時",
      width: "170px",
      render: (row) => formatDateTime(row.created_at),
    },
    { key: "to_number", header: "宛先" },
    { key: "trunk_name", header: "トランク" },
    { key: "pages", header: "ページ数", width: "80px", align: "right" },
    {
      key: "status",
      header: "状態",
      render: (row) => {
        const s = outboxStatus(row);
        return <span className={badge({ tone: s.tone })}>{s.label}</span>;
      },
    },
    {
      key: "attempts",
      header: "試行",
      width: "70px",
      align: "right",
      render: (row) => `${row.attempts}/${row.max_attempts}`,
    },
    {
      key: "last_error",
      header: "結果・エラー",
      render: (row) => {
        if (row.status === "sent") return row.remote_station_id || row.result_text || "—";
        const next =
          row.status === "queued" && row.next_attempt_at
            ? `（次回 ${formatDateTime(row.next_attempt_at)}）`
            : "";
        return row.last_error ? `${row.last_error}${next}` : next || "—";
      },
    },
    {
      key: "actions",
      header: "操作",
      width: "190px",
      align: "right",
      render: (row) => (
        <div className={css({ display: "flex", gap: "1", justifyContent: "flex-end" })}>
          {row.has_pdf ? (
            <a
              href={outboxPdfUrl(row.id)}
              className={button({ variant: "ghost", size: "sm" })}
              aria-label={`送信 FAX ${row.id} の PDF`}
              download
            >
              <Download size={14} />PDF
            </a>
          ) : null}
          {canRetry(row) ? (
            <button
              type="button"
              className={button({ variant: "secondary", size: "sm" })}
              onClick={() => retryMutation.mutate(row.id)}
              disabled={retryMutation.isPending}
              aria-label={`送信 FAX ${row.id} を再送`}
            >
              <RotateCcw size={14} />再送
            </button>
          ) : null}
          {canCancel(row) ? (
            <button
              type="button"
              className={button({ variant: "ghost", size: "sm" })}
              onClick={() => setCancelTarget(row)}
              aria-label={`送信 FAX ${row.id} を取り消す`}
            >
              <X size={14} />取消
            </button>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "5" })}>
      <form
        onSubmit={onSubmit}
        className={cx(panel(), css({ p: "4", display: "flex", flexDirection: "column", gap: "4" }))}
        aria-label="FAX 送信フォーム"
        noValidate
      >
        <div
          className={css({
            display: "grid",
            gap: "4",
            gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))",
          })}
        >
          <Field
            label="宛先 FAX 番号 *"
            error={errors.toNumber}
            help="0 から始まる国内番号（ハイフン可）。国際番号には送信できません。"
          >
            <input
              className={input()}
              inputMode="tel"
              autoComplete="off"
              placeholder="0312345678"
              value={form.toNumber}
              onChange={(e) => {
                const toNumber = e.target.value;
                setForm((f) => ({ ...f, toNumber }));
                setErrors((er) => ({ ...er, toNumber: undefined }));
              }}
            />
          </Field>
          <Field label="送信に使うトランク" help="HGW の同時通話数を超えないよう、送信は順番に行います。">
            <select
              className={input()}
              value={form.trunkName}
              onChange={(e) => {
                const trunkName = e.target.value;
                setForm((f) => ({ ...f, trunkName }));
              }}
            >
              <option value="">
                {defaultTrunk ? `既定の発信トランク（${defaultTrunk.display_name || defaultTrunk.name}）` : "既定の発信トランク"}
              </option>
              {enabledTrunks.map((t) => (
                <option key={t.id} value={t.name}>
                  {t.display_name && t.display_name !== t.name ? `${t.display_name}（${t.name}）` : t.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            data-testid="fax-drop-zone"
            className={css({
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "2",
              p: "5",
              borderWidth: "2px",
              borderStyle: "dashed",
              borderColor: errors.file ? "danger" : dragging ? "accent" : "border",
              borderRadius: "md",
              cursor: "pointer",
              textAlign: "center",
              fontSize: "sm",
              color: "text.muted",
              bg: dragging ? "accent.soft" : "transparent",
            })}
          >
            <FileUp size={20} aria-hidden />
            {form.file ? (
              <span className={css({ color: "text", fontWeight: "medium" })}>
                {form.file.name}（{formatSize(form.file.size)}）
              </span>
            ) : (
              <span>送信する PDF をここにドロップするか、クリックして選択（20 MB・100 ページまで）</span>
            )}
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              aria-label="送信する PDF"
              className={css({ srOnly: true })}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          {errors.file ? (
            <p role="alert" className={css({ color: "danger.text", fontSize: "sm", mt: "1" })}>
              {errors.file}
            </p>
          ) : null}
        </div>
        <div className={css({ display: "flex", justifyContent: "flex-end" })}>
          <button
            type="submit"
            className={button({ variant: "primary" })}
            disabled={sendMutation.isPending}
          >
            <Send size={14} />
            {sendMutation.isPending ? "変換中…" : "送信"}
          </button>
        </div>
      </form>

      <section aria-label="送信ボックス" className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
        <h2 className={css({ fontSize: "md", fontWeight: "semibold" })}>送信ボックス</h2>
        <DataTable
          columns={columns}
          rows={query.data?.items ?? []}
          rowKey={(row) => row.id}
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
          emptyMessage="送信した FAX はまだありません。"
          storageKey="outbox"
        />
        <div
          className={css({
            display: "flex",
            gap: "2",
            alignItems: "center",
            justifyContent: "flex-end",
            mt: "1",
          })}
        >
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || query.isFetching}
          >
            前へ
          </button>
          <span className={css({ fontSize: "sm", color: "text.muted" })}>
            {page} / {pages}（全 {total} 件）
          </span>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= pages || query.isFetching}
          >
            次へ
          </button>
        </div>
      </section>

      <ConfirmDialog
        open={cancelTarget !== null}
        title="送信を取り消す"
        message={cancelTarget ? `${cancelTarget.to_number} への FAX 送信を取り消します。` : ""}
        confirmLabel="取り消す"
        destructive
        busy={cancelMutation.isPending}
        onConfirm={() => cancelTarget && cancelMutation.mutate(cancelTarget.id)}
        onCancel={() => setCancelTarget(null)}
      />
    </div>
  );
}
