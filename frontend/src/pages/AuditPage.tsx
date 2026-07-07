/**
 * 監査ログページ（Phase 6 T9b）。/audit 管理者専用。
 *
 *   - 監査ログ一覧（timestamp / action / actor_label / ip_address / target / detail）
 *   - limit / offset による簡易ページネーション（新しい順）
 */

import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useState } from "react";
import { ScrollText, ChevronLeft, ChevronRight } from "lucide-react";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

import { api } from "../api/client";
import { auditKey } from "../queryKeys";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import {
  canNext,
  canPrev,
  formatTarget,
  formatTimestamp,
  stepOffset,
  type AuditLogRead,
} from "./audit/format";

const PAGE_SIZE = 50;

async function fetchAudit(limit: number, offset: number): Promise<AuditLogRead[]> {
  const { data, error } = await api.GET("/api/audit", {
    params: { query: { limit, offset } },
  });
  if (error) throw new Error("監査ログの取得に失敗しました");
  return (data ?? []) as AuditLogRead[];
}

export function AuditPage() {
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: auditKey(PAGE_SIZE, offset),
    queryFn: () => fetchAudit(PAGE_SIZE, offset),
    placeholderData: keepPreviousData,
  });

  const rows = query.data ?? [];

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
    <PageLayout title="監査ログ" description="システム操作の記録（新しい順）">
      <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
        <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
          <ScrollText size={18} className={css({ color: "accent" })} />
          <span className={css({ fontSize: "sm", color: "text.muted" })}>
            {offset + 1}–{offset + rows.length} 件目
          </span>
        </div>

        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(l) => l.id}
          loading={query.isLoading}
          emptyMessage="監査ログがありません"
        />

        {/* ページネーション */}
        <div className={css({ display: "flex", justifyContent: "flex-end", gap: "2" })}>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            style={{ height: "32px" }}
            disabled={!canPrev(offset) || query.isFetching}
            onClick={() => setOffset((o) => stepOffset(o, PAGE_SIZE, -1))}
          >
            <ChevronLeft size={14} />
            前へ
          </button>
          <button
            type="button"
            className={button({ variant: "secondary", size: "sm" })}
            style={{ height: "32px" }}
            disabled={!canNext(rows.length, PAGE_SIZE) || query.isFetching}
            onClick={() => setOffset((o) => stepOffset(o, PAGE_SIZE, 1))}
          >
            次へ
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </PageLayout>
  );
}
