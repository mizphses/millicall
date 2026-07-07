/**
 * システム管理ページ（Phase 6 T9b）。/system 管理者専用。
 *
 *   - コンテナ一覧（name / image / state / status）+ 再起動ボタン（managed のみ, ConfirmDialog）
 *   - システム情報（/api/system/info）
 *   - 503（Docker 管理無効）を「Docker 管理は無効です」と表示
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ServerCog, RotateCw, Info } from "lucide-react";

import { css, cx } from "styled-system/css";
import { badge, button, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { SYSTEM_CONTAINERS_KEY, SYSTEM_INFO_KEY } from "../queryKeys";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DataTable, type Column } from "../components/DataTable";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";
import {
  normalizeContainers,
  stateTone,
  systemInfoEntries,
  type ContainerRow,
} from "./system/format";

/** Docker 管理が無効（503）であることを示すセンチネルエラー。 */
class DockerDisabledError extends Error {}

// ─────────────────────────────────────────────────────────
// API 関数
// ─────────────────────────────────────────────────────────

async function fetchContainers(): Promise<ContainerRow[]> {
  const { data, error, response } = await api.GET("/api/system/containers");
  if (response.status === 503) throw new DockerDisabledError();
  if (error || !data) throw new Error("コンテナ一覧の取得に失敗しました");
  return normalizeContainers(data as Record<string, unknown>[]);
}

async function fetchSystemInfo(): Promise<Record<string, unknown>> {
  const { data, error, response } = await api.GET("/api/system/info");
  if (response.status === 503) throw new DockerDisabledError();
  if (error || !data) throw new Error("システム情報の取得に失敗しました");
  return data as Record<string, unknown>;
}

async function restartContainer(name: string): Promise<void> {
  const { error, response } = await api.POST("/api/system/containers/{name}/restart", {
    params: { path: { name } },
  });
  if (response.status === 503) throw new DockerDisabledError();
  if (response.status === 404) throw new Error("対象のコンテナが見つかりません");
  if (error) throw new Error("コンテナの再起動に失敗しました");
}

// ─────────────────────────────────────────────────────────
// ページ
// ─────────────────────────────────────────────────────────

export function SystemPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const containersQuery = useQuery({
    queryKey: SYSTEM_CONTAINERS_KEY,
    queryFn: fetchContainers,
    retry: false,
  });
  const infoQuery = useQuery({
    queryKey: SYSTEM_INFO_KEY,
    queryFn: fetchSystemInfo,
    retry: false,
  });

  const [restartTarget, setRestartTarget] = useState<ContainerRow | null>(null);

  const restartMutation = useMutation({
    mutationFn: () => {
      if (!restartTarget) throw new Error();
      return restartContainer(restartTarget.name);
    },
    onSuccess: () => {
      toast.success(`「${restartTarget?.name}」を再起動しました`);
      setRestartTarget(null);
      queryClient.invalidateQueries({ queryKey: SYSTEM_CONTAINERS_KEY });
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "再起動に失敗しました");
      setRestartTarget(null);
    },
  });

  const dockerDisabled =
    containersQuery.error instanceof DockerDisabledError ||
    infoQuery.error instanceof DockerDisabledError;

  const columns: Column<ContainerRow>[] = [
    {
      key: "name",
      header: "コンテナ名",
      render: (c) => (
        <span className={css({ fontWeight: "500", fontFamily: "mono", fontSize: "sm" })}>{c.name}</span>
      ),
    },
    {
      key: "image",
      header: "イメージ",
      render: (c) => (
        <span className={css({ fontSize: "sm", color: "text.muted", fontFamily: "mono" })}>{c.image}</span>
      ),
    },
    {
      key: "state",
      header: "状態",
      render: (c) => <span className={badge({ tone: stateTone(c.state) })}>{c.state || "—"}</span>,
    },
    {
      key: "status",
      header: "詳細",
      render: (c) => <span className={css({ fontSize: "sm", color: "text.muted" })}>{c.status || "—"}</span>,
    },
    {
      key: "_actions",
      header: "",
      width: "120px",
      align: "right",
      render: (c) =>
        c.managed ? (
          <button
            type="button"
            className={button({ variant: "ghost", size: "sm" })}
            title="再起動"
            onClick={() => setRestartTarget(c)}
          >
            <RotateCw size={14} />
            再起動
          </button>
        ) : null,
    },
  ];

  return (
    <PageLayout title="システム" description="コンテナの状態確認・再起動とシステム情報">
      {dockerDisabled ? (
        <div className={cx(panel(), css({ p: "6", textAlign: "center", color: "text.muted" }))}>
          <ServerCog size={32} className={css({ mx: "auto", mb: "3", color: "text.subtle" })} />
          <p className={css({ fontWeight: "600", fontSize: "md" })}>Docker 管理は無効です</p>
          <p className={css({ fontSize: "sm", mt: "1" })}>
            この環境では Docker コンテナの管理機能が利用できません。
          </p>
        </div>
      ) : (
        <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>
          {/* コンテナ一覧 */}
          <div className={css({ display: "flex", flexDirection: "column", gap: "3" })}>
            <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
              <ServerCog size={18} className={css({ color: "accent" })} />
              <h2 className={css({ fontWeight: "600", fontSize: "md" })}>コンテナ</h2>
            </div>
            <DataTable
              columns={columns}
              rows={containersQuery.data ?? []}
              rowKey={(c) => c.name}
              loading={containersQuery.isLoading}
              emptyMessage="コンテナがありません"
            />
          </div>

          {/* システム情報 */}
          <div className={css({ display: "flex", flexDirection: "column", gap: "3" })}>
            <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
              <Info size={18} className={css({ color: "accent" })} />
              <h2 className={css({ fontWeight: "600", fontSize: "md" })}>システム情報</h2>
            </div>
            <div className={cx(panel(), css({ p: "5" }))}>
              {infoQuery.isLoading ? (
                <p className={css({ color: "text.muted", fontSize: "sm" })}>読み込み中…</p>
              ) : infoQuery.data ? (
                <dl
                  className={css({
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                    gap: "3",
                  })}
                >
                  {systemInfoEntries(infoQuery.data).map(([label, value]) => (
                    <div key={label}>
                      <dt className={css({ fontSize: "sm", color: "text.muted" })}>{label}</dt>
                      <dd className={css({ fontSize: "md", fontWeight: "500", wordBreak: "break-all" })}>
                        {value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className={css({ color: "text.muted", fontSize: "sm" })}>情報を取得できませんでした</p>
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!restartTarget}
        title="コンテナを再起動"
        message={
          restartTarget ? `「${restartTarget.name}」を再起動します。よろしいですか？` : ""
        }
        confirmLabel="再起動"
        busy={restartMutation.isPending}
        onConfirm={() => restartMutation.mutate()}
        onCancel={() => setRestartTarget(null)}
      />
    </PageLayout>
  );
}
