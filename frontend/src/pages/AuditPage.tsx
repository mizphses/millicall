/**
 * 監査ログページ（Phase 6 T9b）。/audit 管理者専用。
 *
 *   - 監査ログ一覧（timestamp / action / actor_label / ip_address / target / detail）
 *   - limit / offset による簡易ページネーション（新しい順）
 */

import { useQuery } from "@tanstack/react-query";
import { ScrollText, ChevronLeft, ChevronRight } from "lucide-react";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

import { api } from "../api/client";
import { auditKey } from "../queryKeys";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import {
  formatTarget,
  formatTimestamp,
  type AuditLogRead,
} from "./audit/format";

import { HistoryFilters, resultRange, useHistoryFilters } from "./cdr/historyFilters";

const PAGE_SIZE = 50;

async function fetchAudit(limit: number, offset: number, filters: ReturnType<typeof useHistoryFilters>["params"]): Promise<AuditLogRead[]> {
  const { data, error } = await api.GET("/api/audit", {
    params: { query: { limit, offset, ...filters } },
  });
  if (error) throw new Error("監査ログの取得に失敗しました");
  return (data ?? []) as AuditLogRead[];
}

export function AuditPage() {
  const { page, setPage, filters, params, apply } = useHistoryFilters();
  const offset = (page - 1) * PAGE_SIZE;

  const query = useQuery({
    queryKey: [...auditKey(PAGE_SIZE, offset), params],
    queryFn: () => fetchAudit(PAGE_SIZE + 1, offset, params),
  });

  const rows = (query.data ?? []).slice(0, PAGE_SIZE);
  const hasNext = (query.data?.length ?? 0) > PAGE_SIZE;

  const columns: Column<AuditLogRead>[] = [
    {
      key: "created_at",
      header: "日時",
      width: "170px",
      render: (l) => (
        <span className={css({ fontSize: "sm", fontFamily: "mono", whiteSpace: "nowrap" })}>
          {formatTimestamp(l.created_at)}
        </span>
      ),
    },
    {
      key: "action",
      header: "アクション",
      render: (l) => (
        <span className={css({ fontSize: "sm", fontWeight: "500", fontFamily: "mono" })}>
          {l.action}
        </span>
      ),
    },
    {
      key: "actor_label",
      header: "実行者",
      render: (l) => <span className={css({ fontSize: "sm" })}>{l.actor_label}</span>,
    },
    {
      key: "ip_address",
      header: "IP アドレス",
      render: (l) => (
        <span className={css({ fontSize: "sm", color: "text.muted", fontFamily: "mono" })}>
          {l.ip_address ?? "—"}
        </span>
      ),
    },
    {
      key: "_target",
      header: "対象",
      render: (l) => (
        <span className={css({ fontSize: "sm", color: "text.muted" })}>{formatTarget(l)}</span>
      ),
    },
    {
      key: "detail",
      header: "詳細",
      render: (l) => (
        <span className={css({ fontSize: "sm", color: "text.muted", wordBreak: "break-word" })}>
          {l.detail ?? "—"}
        </span>
      ),
    },
  ];

  return (
    <PageLayout title="監査ログ" description="操作対象・実行者・期間からシステム操作の記録を確認します。">
      <HistoryFilters value={filters} onChange={apply} searchLabel="対象・実行者・操作で検索" />
      <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
        <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
          <ScrollText size={18} className={css({ color: "accent" })} />
          <span aria-live="polite" className={css({ fontSize: "sm", color: "text.muted" })}>
            {query.isLoading ? "読み込み中…" : query.isError ? "取得できませんでした" : `${resultRange(offset, rows.length)} · ページ ${page}`}
          </span>
        </div>

        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(l) => l.id}
          loading={query.isLoading}
          error={query.error ?? undefined}
          onRetry={() => { void query.refetch(); }}
          searchable={false}
          emptyMessage="該当する監査ログがありません。期間や検索条件を変更してください。"
        />

        {/* ページネーション */}
        <div className={css({ display: "flex", justifyContent: "flex-end", gap: "2" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            disabled={page === 1 || query.isFetching}
            onClick={() => setPage(String(Math.max(1, page - 1)))}
          >
            <ChevronLeft size={14} />
            前へ
          </button>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            disabled={!hasNext || query.isFetching}
            onClick={() => setPage(String(page + 1))}
          >
            次へ
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </PageLayout>
  );
}
