/**
 * セキュリティ設定ページ（Phase 6 T9b）。/settings/security
 *
 * 2FA（TOTP）の登録・無効化を扱う。
 *   - 現在の 2FA 状態（/api/auth/me の totp_enabled）を表示
 *   - 登録: setup → QR / secret 表示 → コード verify → リカバリコードを一度だけ表示
 *   - 再セットアップ / 無効化には現行コードが必要
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ShieldCheck, ShieldOff, Copy, Check, AlertTriangle } from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { AUTH_ME_KEY } from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";
import { generateQrMatrix } from "./security/qr";
import {
  buildSetupBody,
  initialState,
  isValidTotpCode,
  toEnrolling,
  toRecovery,
  type TotpEnrollState,
} from "./security/state";
import type { components } from "../api/schema.d";

type UserRead = components["schemas"]["UserRead"];

// ─────────────────────────────────────────────────────────
// API 関数
// ─────────────────────────────────────────────────────────

async function fetchMe(): Promise<UserRead> {
  const { data, error } = await api.GET("/api/auth/me");
  if (error || !data) throw new Error("ユーザー情報の取得に失敗しました");
  return data;
}

async function totpSetup(totpEnabled: boolean, reauthCode: string) {
  const body = buildSetupBody(totpEnabled, reauthCode);
  const { data, error, response } = await api.POST("/api/auth/totp/setup", {
    body: (body ?? {}) as { code?: string | null },
  });
  if (response.status === 401 || response.status === 403) {
    throw new Error("現在の認証コードが正しくありません");
  }
  if (error || !data) throw new Error("2FA セットアップの開始に失敗しました");
  return data;
}

async function totpVerify(code: string): Promise<string[]> {
  const { data, error, response } = await api.POST("/api/auth/totp/verify", {
    body: { code: code.trim() },
  });
  if (response.status === 400 || response.status === 401) {
    throw new Error("認証コードが正しくありません");
  }
  if (error || !data) throw new Error("2FA の確認に失敗しました");
  return data.recovery_codes;
}

async function totpDisable(code: string): Promise<void> {
  const { error, response } = await api.POST("/api/auth/totp/disable", {
    body: { code: code.trim() },
  });
  if (response.status === 400 || response.status === 401) {
    throw new Error("認証コードが正しくありません");
  }
  if (error) throw new Error("2FA の無効化に失敗しました");
}

// ─────────────────────────────────────────────────────────
// ページ
// ─────────────────────────────────────────────────────────

export function SecurityPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const meQuery = useQuery({ queryKey: AUTH_ME_KEY, queryFn: fetchMe });
  const totpEnabled = meQuery.data?.totp_enabled ?? false;

  const [enrollState, setEnrollState] = useState<TotpEnrollState>(initialState());
  const [reauthCode, setReauthCode] = useState(""); // 再セットアップ/無効化用の現行コード
  const [verifyCode, setVerifyCode] = useState(""); // 新シークレット確認用コード
  const [disableCode, setDisableCode] = useState("");
  const [showDisable, setShowDisable] = useState(false);

  // ─── setup ───
  const setupMutation = useMutation({
    mutationFn: () => totpSetup(totpEnabled, reauthCode),
    onSuccess: (data) => {
      setEnrollState(toEnrolling(data.secret, data.provisioning_uri));
      setVerifyCode("");
      setReauthCode("");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "セットアップに失敗しました"),
  });

  // ─── verify ───
  const verifyMutation = useMutation({
    mutationFn: () => totpVerify(verifyCode),
    onSuccess: (codes) => {
      setEnrollState(toRecovery(codes));
      queryClient.invalidateQueries({ queryKey: AUTH_ME_KEY });
      toast.success("2FA を有効にしました");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "確認に失敗しました"),
  });

  // ─── disable ───
  const disableMutation = useMutation({
    mutationFn: () => totpDisable(disableCode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: AUTH_ME_KEY });
      setShowDisable(false);
      setDisableCode("");
      setEnrollState(initialState());
      toast.success("2FA を無効にしました");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "無効化に失敗しました"),
  });

  return (
    <PageLayout title="セキュリティ設定" description="2 段階認証（2FA / TOTP）の設定">
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>
        {/* ─── 現在の状態 ─── */}
        <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}>
          <div className={css({ display: "flex", alignItems: "center", gap: "3" })}>
            {totpEnabled ? (
              <ShieldCheck size={24} className={css({ color: "success.text" })} />
            ) : (
              <ShieldOff size={24} className={css({ color: "text.subtle" })} />
            )}
            <div>
              <h2 className={css({ fontWeight: "600", fontSize: "md" })}>2 段階認証（TOTP）</h2>
              <p className={css({ fontSize: "sm", color: "text.muted" })}>
                {meQuery.isLoading
                  ? "読み込み中…"
                  : totpEnabled
                  ? "有効です。ログイン時に認証コードが必要です。"
                  : "無効です。認証アプリで 2FA を設定できます。"}
              </p>
            </div>
          </div>

          {/* 操作ボタン: idle 状態のときのみ表示 */}
          {enrollState.step === "idle" && (
            <div className={css({ display: "flex", flexDirection: "column", gap: "3" })}>
              {totpEnabled ? (
                <>
                  {/* 再セットアップ / 無効化には現行コードが必要 */}
                  <div>
                    <label className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
                      現在の認証コード（再設定 / 無効化に必要）
                    </label>
                    <input
                      className={input()}
                      value={reauthCode}
                      onChange={(e) => setReauthCode(e.target.value)}
                      inputMode="numeric"
                      maxLength={32}
                      placeholder="6 桁のコード または リカバリコード"
                    />
                  </div>
                  <div className={css({ display: "flex", gap: "2" })}>
                    <button
                      type="button"
                      className={button({ variant: "secondary" })}
                      style={{ height: "36px" }}
                      disabled={setupMutation.isPending || !reauthCode.trim()}
                      onClick={() => setupMutation.mutate()}
                    >
                      {setupMutation.isPending ? "準備中…" : "再セットアップ"}
                    </button>
                    <button
                      type="button"
                      className={button({ variant: "danger" })}
                      style={{ height: "36px" }}
                      disabled={!reauthCode.trim()}
                      onClick={() => {
                        setDisableCode(reauthCode);
                        setShowDisable(true);
                      }}
                    >
                      2FA を無効にする
                    </button>
                  </div>
                </>
              ) : (
                <div>
                  <button
                    type="button"
                    className={button({ variant: "primary" })}
                    style={{ height: "36px" }}
                    disabled={setupMutation.isPending}
                    onClick={() => setupMutation.mutate()}
                  >
                    {setupMutation.isPending ? "準備中…" : "2FA を設定する"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ─── 登録中: QR + secret + verify ─── */}
        {enrollState.step === "enrolling" && (
          <EnrollingCard
            secret={enrollState.secret}
            provisioningUri={enrollState.provisioningUri}
            verifyCode={verifyCode}
            onVerifyCodeChange={setVerifyCode}
            onVerify={() => verifyMutation.mutate()}
            onCancel={() => {
              setEnrollState(initialState());
              setVerifyCode("");
            }}
            verifying={verifyMutation.isPending}
          />
        )}

        {/* ─── リカバリコード表示（1 回のみ） ─── */}
        {enrollState.step === "recovery" && (
          <RecoveryCard
            codes={enrollState.recoveryCodes}
            onDone={() => setEnrollState(initialState())}
          />
        )}

        {/* ─── 無効化確認 ─── */}
        {showDisable && (
          <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}>
            <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
              <AlertTriangle size={18} className={css({ color: "danger.text" })} />
              <h3 className={css({ fontWeight: "600", fontSize: "md" })}>2FA を無効にする</h3>
            </div>
            <p className={css({ fontSize: "sm", color: "text.muted" })}>
              無効化すると、既存のセッションはすべてログアウトされます。現行の認証コードで本人確認します。
            </p>
            <input
              className={input()}
              value={disableCode}
              onChange={(e) => setDisableCode(e.target.value)}
              inputMode="numeric"
              maxLength={32}
              placeholder="6 桁のコード または リカバリコード"
            />
            <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
              <button
                type="button"
                className={button({ variant: "secondary" })}
                style={{ height: "36px" }}
                onClick={() => {
                  setShowDisable(false);
                  setDisableCode("");
                }}
                disabled={disableMutation.isPending}
              >
                キャンセル
              </button>
              <button
                type="button"
                className={button({ variant: "danger" })}
                style={{ height: "36px" }}
                disabled={disableMutation.isPending || !disableCode.trim()}
                onClick={() => disableMutation.mutate()}
              >
                {disableMutation.isPending ? "無効化中…" : "無効にする"}
              </button>
            </div>
          </div>
        )}
      </div>
    </PageLayout>
  );
}

// ─────────────────────────────────────────────────────────
// 登録中カード
// ─────────────────────────────────────────────────────────

function EnrollingCard({
  secret,
  provisioningUri,
  verifyCode,
  onVerifyCodeChange,
  onVerify,
  onCancel,
  verifying,
}: {
  secret: string;
  provisioningUri: string;
  verifyCode: string;
  onVerifyCodeChange: (v: string) => void;
  onVerify: () => void;
  onCancel: () => void;
  verifying: boolean;
}) {
  const matrix = generateQrMatrix(provisioningUri);

  return (
    <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "5" }))}>
      <div>
        <h3 className={css({ fontWeight: "600", fontSize: "md", mb: "1" })}>1. 認証アプリに登録</h3>
        <p className={css({ fontSize: "sm", color: "text.muted" })}>
          認証アプリ（Google Authenticator など）で QR を読み取るか、下のシークレットを手入力してください。
        </p>
      </div>

      <div className={css({ display: "flex", gap: "5", flexWrap: "wrap", alignItems: "flex-start" })}>
        {matrix ? <QrSvg matrix={matrix} /> : null}

        <div className={css({ display: "flex", flexDirection: "column", gap: "3", flex: "1", minWidth: "240px" })}>
          <CopyRow label="シークレットキー" value={secret} mono />
          <CopyRow label="セットアップ URI" value={provisioningUri} mono />
          {!matrix && (
            <p className={css({ fontSize: "sm", color: "warn.text" })}>
              QR を生成できませんでした。上記シークレットを手入力してください。
            </p>
          )}
        </div>
      </div>

      <div>
        <h3 className={css({ fontWeight: "600", fontSize: "md", mb: "2" })}>2. コードを確認</h3>
        <div className={css({ display: "flex", gap: "2", alignItems: "flex-end", flexWrap: "wrap" })}>
          <div>
            <label className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
              認証コード（6 桁）
            </label>
            <input
              className={input()}
              value={verifyCode}
              onChange={(e) => onVerifyCodeChange(e.target.value)}
              inputMode="numeric"
              maxLength={6}
              placeholder="000000"
              autoFocus
            />
          </div>
          <button
            type="button"
            className={button({ variant: "primary" })}
            style={{ height: "36px" }}
            disabled={verifying || !isValidTotpCode(verifyCode)}
            onClick={onVerify}
          >
            {verifying ? "確認中…" : "確認して有効化"}
          </button>
          <button
            type="button"
            className={button({ variant: "ghost" })}
            style={{ height: "36px" }}
            onClick={onCancel}
            disabled={verifying}
          >
            キャンセル
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// QR SVG（自前 QR 行列を SVG に描画）
// ─────────────────────────────────────────────────────────

function QrSvg({ matrix }: { matrix: boolean[][] }) {
  const size = matrix.length;
  const quiet = 4; // 静音領域
  const dim = size + quiet * 2;
  const cells: string[] = [];
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      if (matrix[r][c]) {
        cells.push(`M${c + quiet},${r + quiet}h1v1h-1z`);
      }
    }
  }
  return (
    <svg
      width={200}
      height={200}
      viewBox={`0 0 ${dim} ${dim}`}
      role="img"
      aria-label="2FA セットアップ QR コード"
      className={css({
        borderWidth: "1px",
        borderStyle: "solid",
        borderColor: "border",
        borderRadius: "md",
        flexShrink: 0,
      })}
      style={{ background: "#fff" }}
    >
      <path d={cells.join("")} fill="#000" shapeRendering="crispEdges" />
    </svg>
  );
}

// ─────────────────────────────────────────────────────────
// リカバリコードカード
// ─────────────────────────────────────────────────────────

function RecoveryCard({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
      toast.success("リカバリコードをコピーしました");
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("コピーに失敗しました");
    }
  };

  return (
    <div className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}>
      <div className={css({ display: "flex", alignItems: "center", gap: "2" })}>
        <AlertTriangle size={18} className={css({ color: "warn.text" })} />
        <h3 className={css({ fontWeight: "600", fontSize: "md" })}>リカバリコードを保存してください</h3>
      </div>
      <p className={css({ fontSize: "sm", color: "text.muted" })}>
        以下のリカバリコードは<strong>この画面でしか表示されません</strong>。
        認証アプリを利用できない場合のログインに使用します。安全な場所に保管してください。
      </p>

      <div
        className={css({
          display: "grid",
          gridTemplateColumns: "repeat(2, 1fr)",
          gap: "2",
          p: "4",
          bg: "gray.50",
          borderRadius: "md",
          borderWidth: "1px",
          borderStyle: "solid",
          borderColor: "border",
        })}
      >
        {codes.map((code) => (
          <span key={code} className={css({ fontFamily: "mono", fontSize: "sm" })}>
            {code}
          </span>
        ))}
      </div>

      <div className={css({ display: "flex", gap: "2", justifyContent: "flex-end" })}>
        <button
          type="button"
          className={button({ variant: "secondary" })}
          style={{ height: "36px" }}
          onClick={copyAll}
        >
          {copied ? <Check size={16} /> : <Copy size={16} />}
          {copied ? "コピー済み" : "すべてコピー"}
        </button>
        <button
          type="button"
          className={button({ variant: "primary" })}
          style={{ height: "36px" }}
          onClick={onDone}
        >
          保存しました
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────
// コピー可能な行
// ─────────────────────────────────────────────────────────

function CopyRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("コピーに失敗しました");
    }
  };
  return (
    <div>
      <label className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
        {label}
      </label>
      <div className={css({ display: "flex", gap: "2", alignItems: "center" })}>
        <code
          className={css({
            flex: "1",
            fontFamily: mono ? "mono" : undefined,
            fontSize: "sm",
            p: "2",
            bg: "gray.50",
            borderRadius: "sm",
            borderWidth: "1px",
            borderStyle: "solid",
            borderColor: "border",
            wordBreak: "break-all",
          })}
        >
          {value}
        </code>
        <button
          type="button"
          className={button({ variant: "ghost", size: "sm" })}
          onClick={copy}
          title="コピー"
        >
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
    </div>
  );
}
