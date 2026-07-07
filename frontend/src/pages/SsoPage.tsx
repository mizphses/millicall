/**
 * SSO / SCIM ページ（Phase 6 T9b, 軽量）。/sso 管理者専用。
 *
 *   - SCIM トークン生成: POST /api/scim/token → 一度だけ表示 + コピー + 警告
 *   - SAML/SSO: env ベース設定のため静的ガイダンス + メタデータ URL リンク
 */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { KeyRound, Copy, Check, AlertTriangle, Link2 } from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";

async function rotateScimToken(): Promise<string> {
  const { data, error } = await api.POST("/api/scim/token");
  if (error || !data) throw new Error("SCIM トークンの生成に失敗しました");
  return (data as { token: string }).token;
}

export function SsoPage() {
  const toast = useToast();
  const [token, setToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const rotateMutation = useMutation({
    mutationFn: rotateScimToken,
    onSuccess: (t) => {
      setToken(t);
      setCopied(false);
      toast.success("SCIM トークンを生成しました");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "生成に失敗しました"),
  });

  const copy = async () => {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("コピーに失敗しました");
    }
  };

  const metadataUrl =
    typeof window !== "undefined" ? `${window.location.origin}/saml/metadata` : "/saml/metadata";

  return (
    <PageLayout title="SSO / SCIM" description="シングルサインオンと SCIM プロビジョニングの設定">
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>
        {/* ─── SCIM トークン ─── */}
        <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}>
          <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
            <KeyRound size={18} className={css({ color: "accent" })} />
            <div>
              <h2 className={css({ fontWeight: "600", fontSize: "md" })}>SCIM トークン</h2>
              <p className={css({ fontSize: "sm", color: "text.muted" })}>
                ID プロバイダから SCIM プロビジョニングを行うためのベアラートークンです。
              </p>
            </div>
          </div>

          {token ? (
            <div className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
              <div
                className={css({
                  display: "flex",
                  alignItems: "center",
                  gap: "2",
                  fontSize: "sm",
                  color: "warn.text",
                })}
              >
                <AlertTriangle size={14} />
                <span>このトークンは一度だけ表示されます。安全な場所に保管してください。</span>
              </div>
              <div className={css({ display: "flex", gap: "2", alignItems: "center" })}>
                <code
                  className={css({
                    flex: "1",
                    fontFamily: "mono",
                    fontSize: "sm",
                    p: "3",
                    bg: "gray.50",
                    borderRadius: "sm",
                    borderWidth: "1px",
                    borderStyle: "solid",
                    borderColor: "border",
                    wordBreak: "break-all",
                  })}
                >
                  {token}
                </code>
                <button
                  type="button"
                  className={button({ variant: "secondary" })}
                  style={{ height: "36px", flexShrink: 0 }}
                  onClick={copy}
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                  {copied ? "コピー済み" : "コピー"}
                </button>
              </div>
              <button
                type="button"
                className={button({ variant: "ghost", size: "sm" })}
                style={{ alignSelf: "flex-start" }}
                onClick={() => rotateMutation.mutate()}
                disabled={rotateMutation.isPending}
              >
                {rotateMutation.isPending ? "生成中…" : "再生成する"}
              </button>
            </div>
          ) : (
            <div>
              <button
                type="button"
                className={button({ variant: "primary" })}
                style={{ height: "36px" }}
                onClick={() => rotateMutation.mutate()}
                disabled={rotateMutation.isPending}
              >
                <KeyRound size={16} />
                {rotateMutation.isPending ? "生成中…" : "SCIM トークンを生成"}
              </button>
              <p className={css({ fontSize: "sm", color: "text.muted", mt: "2" })}>
                生成すると既存のトークンは無効になります。
              </p>
            </div>
          )}
        </div>

        {/* ─── SAML / SSO ─── */}
        <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "3" }))}>
          <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
            <Link2 size={18} className={css({ color: "accent" })} />
            <h2 className={css({ fontWeight: "600", fontSize: "md" })}>SAML シングルサインオン</h2>
          </div>
          <p className={css({ fontSize: "sm", color: "text.muted" })}>
            SAML は環境変数で設定します。IdP・証明書・エンティティ ID をサーバー側の環境変数で構成してください。
            SP メタデータは以下の URL で取得できます。
          </p>
          <a
            href={metadataUrl}
            target="_blank"
            rel="noreferrer"
            className={css({
              display: "inline-flex",
              alignItems: "center",
              gap: "2",
              fontSize: "sm",
              color: "accent.text",
              fontFamily: "mono",
              wordBreak: "break-all",
            })}
          >
            <Link2 size={14} />
            {metadataUrl}
          </a>
        </div>
      </div>
    </PageLayout>
  );
}
