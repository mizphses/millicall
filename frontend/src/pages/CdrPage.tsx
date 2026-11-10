import { useQuery } from "@tanstack/react-query";

import { css } from "styled-system/css";
import { badge, button } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { CDR_KEY, CALL_MESSAGES_KEY } from "../queryKeys";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { SlidePanel } from "../components/SlidePanel";
import { useUrlState } from "../hooks/useUrlState";
import { HistoryFilters, resultRange, useHistoryFilters } from "./cdr/historyFilters";
import { formatDateTime, formatDuration, formatLatency } from "./cdr/format";

type CdrRead = components["schemas"]["CdrRead"];
type CallMessageRead = components["schemas"]["CallMessageRead"];

const PAGE_SIZE = 50;

async function fetchCdr(offset: number, filters: ReturnType<typeof useHistoryFilters>["params"], direction: string): Promise<CdrRead[]> {
  const { data, error } = await api.GET("/api/cdr", {
    params: { query: { limit: PAGE_SIZE + 1, offset, ...filters, direction: direction || undefined } },
  });
  if (error) throw new Error("通話履歴の取得に失敗しました");
  return data ?? [];
}

async function fetchCallMessages(callUuid: string): Promise<CallMessageRead[]> {
  const { data, error } = await api.GET("/api/call-messages", {
    params: { query: { call_uuid: callUuid } },
  });
  if (error) throw new Error("AI 会話ログの取得に失敗しました");
  return data ?? [];
}

/** 発着信の方向バッジ。 */
function DirectionBadge({ direction }: { direction: string }) {
  if (direction === "inbound") {
    return <span className={badge({ tone: "accent" })}>着信</span>;
  }
  if (direction === "outbound") {
    return <span className={badge({ tone: "success" })}>発信</span>;
  }
  return <span className={badge({ tone: "neutral" })}>{direction || "-"}</span>;
}

export function CdrPage() {
  const { page, setPage, filters, params, apply } = useHistoryFilters();
  const offset = (page - 1) * PAGE_SIZE;
  const [selectedId, setSelectedId] = useUrlState("selected", "");
  const listQuery = useQuery({
    queryKey: [...CDR_KEY, offset, params, filters.direction],
    queryFn: () => fetchCdr(offset, params, filters.direction),
  });
  const rows = (listQuery.data ?? []).slice(0, PAGE_SIZE);
  const hasNext = (listQuery.data?.length ?? 0) > PAGE_SIZE;
  const selectedQuery = useQuery({
    queryKey: [...CDR_KEY, "detail", selectedId],
    enabled: /^\d+$/.test(selectedId),
    queryFn: async () => {
      const { data, error } = await api.GET("/api/cdr/{cdr_id}", { params: { path: { cdr_id: Number(selectedId) } } });
      if (error || !data) throw new Error("通話履歴を取得できませんでした。");
      return data;
    },
  });
  const selected = rows.find((row) => String(row.id) === selectedId) ?? selectedQuery.data;

  const columns: Column<CdrRead>[] = [
    {
      key: "direction",
      header: "方向",
      width: "88px",
      render: (row) => <DirectionBadge direction={row.direction} />,
    },
    { key: "src_number", header: "発信元", render: (row) => row.src_number || "-" },
    { key: "dst_number", header: "宛先", render: (row) => row.dst_number || "-" },
    {
      key: "started_at",
      header: "開始時刻",
      render: (row) => formatDateTime(row.started_at),
    },
    {
      key: "duration_seconds",
      header: "通話時間",
      width: "100px",
      align: "right",
      render: (row) => formatDuration(row.duration_seconds),
    },
    {
      key: "hangup_cause",
      header: "終了理由",
      render: (row) => row.hangup_cause || "-",
    },
    {
      key: "detail", header: "詳細", width: "110px", sortable: false,
      render: (row) => <button type="button" className={button({ variant: "secondary", size: "sm" })}
        aria-label={`${row.src_number || "不明"} から ${row.dst_number || "不明"} への通話詳細`}
        onClick={() => setSelectedId(String(row.id), { replace: false })}>詳細を見る</button>,
    },
  ];

  return (
    <PageLayout title="通話履歴" description="発着信の記録を確認し、通話ごとの AI 会話ログを表示します。">
      <HistoryFilters value={filters} onChange={apply} searchLabel="番号・発信者名で検索" withDirection />
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        loading={listQuery.isLoading}
        error={listQuery.error ?? undefined}
        onRetry={() => { void listQuery.refetch(); }}
        searchable={false}
        emptyMessage="該当する通話履歴がありません。期間や検索条件を変更してください。"
      />

      <div
        className={css({
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-end",
          flexWrap: "wrap",
          gap: "3",
          mt: "4",
        })}
      >
        <span className={css({ fontSize: "sm", color: "text.muted" })} aria-live="polite">{listQuery.isLoading ? "読み込み中…" : listQuery.isError ? "取得できませんでした" : `${resultRange(offset, rows.length)} · ページ ${page}`}</span>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          onClick={() => setPage(String(Math.max(1, page - 1)))}
          disabled={offset === 0 || listQuery.isFetching}
        >
          前へ
        </button>
        <button
          type="button"
          className={button({ variant: "secondary", size: "sm" })}
          onClick={() => setPage(String(page + 1))}
          disabled={!hasNext || listQuery.isFetching}
        >
          次へ
        </button>
      </div>

      <SlidePanel
        open={selectedId !== ""}
        title="通話詳細・AI 会話ログ"
        onClose={() => setSelectedId("")}
      >
        {selected ? <ConversationPanel cdr={selected} /> : selectedQuery.isLoading ? <p role="status">読み込み中…</p> :
          <div role="alert"><p>通話履歴を取得できませんでした。削除済み、または取得に失敗した可能性があります。</p>
            <button type="button" className={button({ variant: "secondary", size: "sm" })} onClick={() => { void selectedQuery.refetch(); }}>再試行</button></div>}
      </SlidePanel>
    </PageLayout>
  );
}

/** 選択された CDR の call_uuid に対する会話タイムライン。 */
function ConversationPanel({ cdr }: { cdr: CdrRead }) {
  const query = useQuery({
    queryKey: [...CALL_MESSAGES_KEY, cdr.call_uuid],
    queryFn: () => fetchCallMessages(cdr.call_uuid),
  });

  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "4" })}>
      <dl
        className={css({
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "1",
          fontSize: "sm",
          color: "text.muted",
        })}
      >
        <dt>方向</dt>
        <dd className={css({ color: "text" })}>
          <DirectionBadge direction={cdr.direction} />
        </dd>
        <dt>発信元 / 宛先</dt>
        <dd className={css({ color: "text" })}>
          {cdr.src_number || "-"} → {cdr.dst_number || "-"}
        </dd>
        <dt>開始時刻</dt>
        <dd className={css({ color: "text" })}>{formatDateTime(cdr.started_at)}</dd>
        <dt>通話時間</dt>
        <dd className={css({ color: "text" })}>{formatDuration(cdr.duration_seconds)}</dd>
      </dl>

      {query.isLoading ? (
        <p className={css({ color: "text.muted", fontSize: "md" })}>読み込み中…</p>
      ) : query.isError ? (
        <div role="alert"><p className={css({ color: "danger.text", fontSize: "md" })}>会話ログの取得に失敗しました。</p>
          <button type="button" className={button({ variant: "secondary", size: "sm" })} onClick={() => { void query.refetch(); }}>再試行</button></div>
      ) : (query.data ?? []).length === 0 ? (
        <p className={css({ color: "text.muted", fontSize: "md" })}>AI 応対なし</p>
      ) : (
        <ConversationTimeline messages={query.data ?? []} />
      )}
    </div>
  );
}

function ConversationTimeline({ messages }: { messages: CallMessageRead[] }) {
  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "3" })}>
      {messages.map((m) => {
        const isAssistant = m.role === "assistant";
        return (
          <div
            key={m.id}
            className={css({
              display: "flex",
              flexDirection: "column",
              alignItems: isAssistant ? "flex-end" : "flex-start",
            })}
          >
            <div
              className={css({
                maxWidth: "85%",
                borderWidth: "1px",
                borderStyle: "solid",
                borderRadius: "md",
                px: "3",
                py: "2",
                fontSize: "md",
                bg: isAssistant ? "accent.soft" : "gray.50",
                borderColor: isAssistant ? "accent" : "border",
                color: "text",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              })}
            >
              {m.text || <span className={css({ color: "text.subtle" })}>（無音）</span>}
            </div>
            <div
              className={css({
                display: "flex",
                gap: "2",
                mt: "1",
                alignItems: "center",
              })}
            >
              <span className={css({ fontSize: "sm", color: "text.subtle" })}>
                {isAssistant ? "アシスタント" : "発信者"}
              </span>
              {isAssistant && m.latency_ms != null ? (
                <span className={badge({ tone: "neutral" })}>{formatLatency(m.latency_ms)}</span>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
