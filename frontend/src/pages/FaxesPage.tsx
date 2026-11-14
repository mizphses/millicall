import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Download, Trash2 } from "lucide-react";

import { css } from "styled-system/css";
import { badge, button } from "styled-system/recipes";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { ObjectLink } from "../components/ObjectLink";
import { PageLayout } from "../components/PageLayout";
import { FAXES_KEY, faxesKey } from "../queryKeys";
import { useUrlState } from "../hooks/useUrlState";
import { useToast } from "../toast/ToastProvider";
import { formatDateTime } from "./cdr/format";
import { faxPdfUrl, faxResult, faxSender, totalPages, type FaxRead } from "./faxes/format";
import { OutboxPanel } from "./faxes/OutboxPanel";

const PAGE_SIZE = 50;

async function fetchFaxes(offset: number): Promise<{ items: FaxRead[]; total: number }> {
  const { data, error } = await api.GET("/api/faxes", {
    params: { query: { limit: PAGE_SIZE, offset } },
  });
  if (error || !data) throw new Error("受信 FAX の取得に失敗しました");
  return data;
}

async function deleteFax(id: number): Promise<void> {
  const { error } = await api.DELETE("/api/faxes/{fax_id}", {
    params: { path: { fax_id: id } },
  });
  if (error) throw new Error("FAX の削除に失敗しました");
}

type FaxTab = "inbox" | "outbox";

const TABS: { value: FaxTab; label: string }[] = [
  { value: "inbox", label: "受信" },
  { value: "outbox", label: "送信" },
];

const tabButton = css({
  px: "4",
  py: "2",
  fontSize: "sm",
  fontWeight: "medium",
  borderBottomWidth: "2px",
  borderBottomStyle: "solid",
  borderBottomColor: "transparent",
  color: "text.muted",
  cursor: "pointer",
  bg: "transparent",
  "&[aria-selected=true]": { borderBottomColor: "accent", color: "text" },
});

/** FAX（受信・送信タブ）。タブは URL の ?tab= に保持する。 */
export function FaxesPage() {
  const [tabParam, setTab] = useUrlState("tab", "inbox");
  const tab: FaxTab = tabParam === "outbox" ? "outbox" : "inbox";
  return (
    <PageLayout
      title="FAX"
      description="受信した FAX の確認と、PDF をアップロードして外線トランク経由で FAX を送信できます。"
    >
      <div
        role="tablist"
        aria-label="FAX の表示切替"
        className={css({ display: "flex", gap: "1", borderBottomWidth: "1px", borderColor: "border" })}
      >
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            id={`fax-tab-${t.value}`}
            aria-selected={tab === t.value}
            aria-controls={`fax-panel-${t.value}`}
            className={tabButton}
            onClick={() => setTab(t.value)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`fax-panel-${tab}`} aria-labelledby={`fax-tab-${tab}`}>
        {tab === "outbox" ? <OutboxPanel /> : <InboxPanel />}
      </div>
    </PageLayout>
  );
}

/** 受信 FAX の一覧（PDF ダウンロード・削除）。 */
function InboxPanel() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const offset = (page - 1) * PAGE_SIZE;
  const query = useQuery({ queryKey: faxesKey(PAGE_SIZE, offset), queryFn: () => fetchFaxes(offset) });
  const [deleteTarget, setDeleteTarget] = useState<FaxRead | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteFax(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: FAXES_KEY });
      toast.success("FAX を削除しました");
      setDeleteTarget(null);
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "削除に失敗しました"),
  });

  const total = query.data?.total ?? 0;
  const pages = totalPages(total, PAGE_SIZE);

  const columns: Column<FaxRead>[] = [
    {
      key: "received_at",
      header: "受信日時",
      width: "180px",
      render: (row) => formatDateTime(row.received_at),
    },
    { key: "from_number", header: "発信元", render: (row) => faxSender(row) },
    { key: "to_number", header: "宛先", render: (row) => row.to_number || "—" },
    { key: "pages", header: "ページ数", width: "90px", align: "right" },
    {
      key: "success",
      header: "結果",
      render: (row) => {
        const result = faxResult(row);
        return <span className={badge({ tone: result.tone })}>{result.label}</span>;
      },
    },
    {
      key: "download",
      header: "ダウンロード",
      width: "130px",
      render: (row) =>
        row.has_pdf ? (
          <a
            href={faxPdfUrl(row.id)}
            className={button({ variant: "secondary", size: "sm" })}
            download
          >
            <Download size={14} />PDF
          </a>
        ) : (
          "—"
        ),
    },
    {
      key: "actions",
      header: "削除",
      width: "100px",
      align: "right",
      render: (row) => (
        <button
          type="button"
          className={button({ variant: "ghost", size: "sm" })}
          onClick={() => setDeleteTarget(row)}
          aria-label={`FAX ${row.id} を削除`}
        >
          <Trash2 size={14} />削除
        </button>
      ),
    },
  ];

  return (
    <div className={css({ display: "flex", flexDirection: "column" })}>
      <p className={css({ fontSize: "sm", color: "text.muted", mb: "3" })}>
        FAX 受信番号で受け取った FAX の一覧です。受信番号の追加・トランクへの割り当ては<ObjectLink to="/routes">ルーティング</ObjectLink>
        と<ObjectLink to="/trunks">外線トランク</ObjectLink>で行います。
      </p>
      <DataTable
        columns={columns}
        rows={query.data?.items ?? []}
        rowKey={(row) => row.id}
        loading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        emptyMessage="受信した FAX はまだありません。"
      />
      <div
        className={css({
          display: "flex",
          gap: "2",
          alignItems: "center",
          justifyContent: "flex-end",
          mt: "3",
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

      <ConfirmDialog
        open={deleteTarget !== null}
        title="FAX を削除"
        message={
          deleteTarget
            ? `${formatDateTime(deleteTarget.received_at)} に ${faxSender(deleteTarget)} から受信した FAX（PDF・原本 TIFF）を削除します。この操作は取り消せません。`
            : ""
        }
        confirmLabel="削除"
        destructive
        busy={deleteMutation.isPending}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
