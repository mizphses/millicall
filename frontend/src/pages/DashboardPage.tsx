import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { css } from "styled-system/css";
import { badge } from "styled-system/recipes";

import { api } from "../api/client";
import type { components } from "../api/schema";
import { DASHBOARD_KEYS } from "../queryKeys";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { formatDateTime, formatDuration } from "./cdr/format";

type CdrRead = components["schemas"]["CdrRead"];

const RECENT_LIMIT = 100;

async function fetchCount(path: "/api/extensions" | "/api/trunks" | "/api/ai-agents"): Promise<number> {
  const { data, error } = await api.GET(path);
  if (error) throw new Error("件数の取得に失敗しました");
  return (data ?? []).length;
}

async function fetchRecentCdr(): Promise<CdrRead[]> {
  const { data, error } = await api.GET("/api/cdr", {
    params: { query: { limit: RECENT_LIMIT, offset: 0 } },
  });
  if (error) throw new Error("通話履歴の取得に失敗しました");
  return data ?? [];
}

export function DashboardPage() {
  const extensionsQuery = useQuery({
    queryKey: DASHBOARD_KEYS.extensionsCount,
    queryFn: () => fetchCount("/api/extensions"),
  });
  const trunksQuery = useQuery({
    queryKey: DASHBOARD_KEYS.trunksCount,
    queryFn: () => fetchCount("/api/trunks"),
  });
  const aiAgentsQuery = useQuery({
    queryKey: DASHBOARD_KEYS.aiAgentsCount,
    queryFn: () => fetchCount("/api/ai-agents"),
  });
  const cdrQuery = useQuery({
    queryKey: DASHBOARD_KEYS.recentCdr,
    queryFn: fetchRecentCdr,
  });

  const cdrRows = cdrQuery.data ?? [];
  const cdrCountLabel =
    cdrRows.length >= RECENT_LIMIT ? `${RECENT_LIMIT}+` : String(cdrRows.length);

  const columns: Column<CdrRead>[] = [
    {
      key: "direction",
      header: "方向",
      width: "88px",
      render: (row) =>
        row.direction === "inbound" ? (
          <span className={badge({ tone: "accent" })}>着信</span>
        ) : row.direction === "outbound" ? (
          <span className={badge({ tone: "success" })}>発信</span>
        ) : (
          <span className={badge({ tone: "neutral" })}>{row.direction || "-"}</span>
        ),
    },
    { key: "src_number", header: "発信元", render: (row) => row.src_number || "-" },
    { key: "dst_number", header: "宛先", render: (row) => row.dst_number || "-" },
    { key: "started_at", header: "開始時刻", render: (row) => formatDateTime(row.started_at) },
    {
      key: "duration_seconds",
      header: "通話時間",
      width: "100px",
      align: "right",
      render: (row) => formatDuration(row.duration_seconds),
    },
  ];

  return (
    <PageLayout title="ダッシュボード" description="内線・トランク・AI エージェント件数と直近の通話">
      <div
        className={css({
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: "4",
          mb: "6",
        })}
      >
        <StatCard
          label="内線数"
          value={extensionsQuery.data}
          loading={extensionsQuery.isLoading}
          error={extensionsQuery.isError}
          to="/extensions"
        />
        <StatCard
          label="トランク数"
          value={trunksQuery.data}
          loading={trunksQuery.isLoading}
          error={trunksQuery.isError}
          to="/trunks"
        />
        <StatCard
          label="AI エージェント数"
          value={aiAgentsQuery.data}
          loading={aiAgentsQuery.isLoading}
          error={aiAgentsQuery.isError}
          to="/ai-agents"
        />
        <StatCard
          label="直近の通話"
          valueLabel={cdrCountLabel}
          loading={cdrQuery.isLoading}
          error={cdrQuery.isError}
          to="/cdr"
        />
      </div>

      <div
        className={css({
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          mb: "3",
        })}
      >
        <h2 className={css({ fontSize: "lg", fontWeight: "600", color: "text" })}>直近の通話履歴</h2>
        <Link to="/cdr" className={css({ fontSize: "sm", color: "accent.text" })}>
          すべて表示 →
        </Link>
      </div>

      <DataTable
        columns={columns}
        rows={cdrRows.slice(0, 10)}
        rowKey={(row) => row.id}
        loading={cdrQuery.isLoading}
        emptyMessage="通話履歴がまだありません。"
      />
    </PageLayout>
  );
}

function StatCard({
  label,
  value,
  valueLabel,
  loading,
  error,
  to,
}: {
  label: string;
  value?: number;
  valueLabel?: string;
  loading: boolean;
  error?: boolean;
  to: string;
}) {
  const display = loading
    ? "…"
    : error
      ? "取得エラー"
      : (valueLabel ?? (value != null ? String(value) : "-"));

  return (
    <Link
      to={to}
      className={css({
        display: "block",
        borderWidth: "1px",
        borderStyle: "solid",
        borderColor: "border",
        borderRadius: "lg",
        bg: "white",
        px: "5",
        py: "4",
        transition: "border-color 0.12s",
        _hover: { borderColor: "accent" },
      })}
    >
      <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "2" })}>
        {label}
      </span>
      <span
        className={css({
          display: "block",
          fontSize: "xl",
          fontWeight: "600",
          color: error ? "danger.text" : "text",
        })}
      >
        {display}
      </span>
    </Link>
  );
}
