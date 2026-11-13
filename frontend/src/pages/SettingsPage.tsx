/**
 * 設定ページ（管理者専用）。GET/PUT /api/settings。
 *
 * .env でしか変更できなかった運用設定を管理画面から編集する:
 *   - SSO / プロビジョニング（SAML / SCIM）
 *   - メール (SMTP)
 *   - 認証ポリシー（TOTP / ログインレート制限 / セッション）
 *   - 音声 AI チューニング（VAD / 再生タイムアウト）
 *   - 電話運用（国際発信 allowlist / 匿名着信拒否 / MCP / 電話機管理者資格情報）
 *   - ネットワーク（Tailscale Serve）
 *
 * 秘密値（SMTP パスワード / 電話機管理者パスワード）は書き込み専用:
 * 「設定済み」表示 + 上書き入力のパターン（NetworkPage の auth key と同じ）。
 * DB 上書きされたキーには「既定に戻す」ボタンを表示し、.env の値へ戻せる。
 *
 * デザイン原則: PandaCSS css()/cx() + recipes (panel, button, input)、lucide-react、
 * NetworkPage / ProvidersPage のハウススタイルに準拠。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { acceptSavedForm, mergeServerForm, FormValidationError, apiError } from "./settings/formState";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  Globe,
  KeyRound,
  Mail,
  MicVocal,
  Phone,
  RotateCcw,
  Shield,
  ShieldCheck,
  Wifi,
  Webhook,
} from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { APP_SETTINGS_KEY } from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { UnsavedChangesGuard } from "../components/UnsavedChangesGuard";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../toast/ToastProvider";
import { ScimRoleMapField } from "./settings/ScimRoleMapField";
import { roleMapToRowsJson, rowsJsonToRoleMap } from "./settings/scimRoleMap";
import {
  idpMetadataToFormValues,
  isHttpsProtocol,
  scimTenantUrlFromOrigin,
  spValuesFromOrigin,
  validateMetadataUrl,
  type IdpMetadataResponse,
} from "./settings/ssoSetup";

// ---------------------------------------------------------------------------
// API 型（schema.d.ts の SettingsRead / SettingsUpdate と対応）
// ---------------------------------------------------------------------------

type SettingsRead = {
  values: Record<string, unknown>;
  overridden: string[];
  secrets: Record<string, boolean>;
};

type SettingsUpdate = {
  values?: Record<string, unknown>;
  reset?: string[];
};

async function fetchSettings(): Promise<SettingsRead> {
  const { data, error } = await api.GET("/api/settings");
  if (error || !data) throw new Error("設定の取得に失敗しました");
  return data as SettingsRead;
}

async function updateSettings(payload: SettingsUpdate): Promise<SettingsRead> {
  const { data, error, response } = await api.PUT("/api/settings", { body: payload });
  if (response.status === 400) {
    throw apiError(error, "設定値が不正です");
  }
  if (error || !data) throw apiError(error, "設定の保存に失敗しました");
  return data as SettingsRead;
}

/** IdP フェデレーションメタデータ URL から SAML 設定値を取得する（保存はされない）。 */
async function fetchIdpMetadata(url: string): Promise<IdpMetadataResponse> {
  const { data, error, response } = await api.POST("/api/settings/saml/fetch-idp-metadata", {
    body: { url },
  });
  if (response.status === 400) {
    throw apiError(error, "IdP メタデータの取得に失敗しました");
  }
  if (error || !data) throw new Error("IdP メタデータの取得に失敗しました");
  return data as IdpMetadataResponse;
}

/** SCIM Bearer トークンを発行（再発行）する。古いトークンは即座に無効になる。 */
async function rotateScimToken(): Promise<string> {
  const { data, error } = await api.POST("/api/scim/token");
  if (error || !data) throw new Error("SCIM トークンの発行に失敗しました");
  return (data as { token: string }).token;
}

// ---------------------------------------------------------------------------
// フィールド / セクション定義
// ---------------------------------------------------------------------------

type FieldDef = {
  key: string;
  label: string;
  type: "text" | "number" | "boolean" | "secret" | "textarea" | "select" | "scim_role_map";
  options?: { value: string; label: string }[];
  placeholder?: string;
  /** 入力欄の下に出す補足説明。 */
  help?: string;
  /** number 入力を小数として解釈する（playback_timeout_sec 用）。 */
  float?: boolean;
  /** 空欄を null として送る（mcp_default_agent_id 用）。 */
  nullable?: boolean;
  min?: number;
  max?: number;
};

type SectionDef = {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  fields: FieldDef[];
  /** セクション先頭に出す注意書き（再起動が必要な項目など）。 */
  note?: string;
};

const SECTIONS: SectionDef[] = [
  {
    id: "sso",
    title: "SSO / プロビジョニング",
    description: "SAML シングルサインオンと SCIM プロビジョニングの設定",
    icon: <KeyRound size={18} />,
    fields: [
      { key: "saml_enabled", label: "SAML SSO を有効にする", type: "boolean" },
      {
        key: "saml_sp_entity_id",
        label: "SP Entity ID",
        type: "text",
        placeholder: "https://millicall.example.com/saml/metadata",
      },
      {
        key: "saml_sp_acs_url",
        label: "ACS URL（POST binding）",
        type: "text",
        placeholder: "https://millicall.example.com/saml/acs",
      },
      { key: "saml_idp_entity_id", label: "IdP Entity ID", type: "text" },
      {
        key: "saml_idp_sso_url",
        label: "IdP SSO URL（HTTP-Redirect binding）",
        type: "text",
        placeholder: "https://idp.example.com/sso",
      },
      {
        key: "saml_idp_x509_cert",
        label: "IdP X.509 証明書（PEM 形式）",
        type: "textarea",
        placeholder: "-----BEGIN CERTIFICATE-----",
        help: "この証明書のみを信頼します（IdP から事前共有された値を貼り付けてください）。",
      },
      {
        key: "saml_default_role",
        label: "新規ユーザーのデフォルトロール",
        type: "select",
        options: [
          { value: "user", label: "user" },
          { value: "admin", label: "admin" },
        ],
      },
      {
        key: "saml_allowed_clock_skew_seconds",
        label: "許容クロックスキュー（秒）",
        type: "number",
      },
      { key: "scim_enabled", label: "SCIM プロビジョニングを有効にする", type: "boolean" },
      {
        key: "scim_group_role_map",
        label: "SCIM グループ → ロール割り当て",
        type: "scim_role_map",
        help: "IdP から同期したグループの displayName に一致するメンバーへロールを自動付与します（admin が最優先）。どの割り当て済みグループにも属さない SCIM ユーザーは user に戻ります。空にすると自動付与は無効です。",
      },
    ],
  },
  {
    id: "email",
    title: "メール (SMTP)",
    description: "ワークフローのメール送信に使う SMTP サーバーの設定",
    icon: <Mail size={18} />,
    fields: [
      {
        key: "smtp_host",
        label: "SMTP ホスト",
        type: "text",
        placeholder: "smtp.example.com",
        help: "空にするとメール送信は無効になります。",
      },
      { key: "smtp_port", label: "SMTP ポート", type: "number", placeholder: "587" },
      { key: "smtp_username", label: "SMTP ユーザー名", type: "text" },
      { key: "smtp_password", label: "SMTP パスワード", type: "secret" },
      {
        key: "smtp_from",
        label: "From アドレス",
        type: "text",
        help: "空の場合は SMTP ユーザー名を From に使います。",
      },
      { key: "smtp_starttls", label: "STARTTLS を使用する", type: "boolean" },
      { key: "smtp_timeout", label: "タイムアウト（秒）", type: "number" },
    ],
  },
  {
    id: "auth",
    title: "認証ポリシー",
    description: "二要素認証・ログインレート制限・セッション有効期間",
    icon: <ShieldCheck size={18} />,
    fields: [
      { key: "totp_required", label: "全ユーザーに TOTP 登録を必須にする", type: "boolean" },
      { key: "totp_ticket_max_age", label: "TOTP チケット有効期間（秒）", type: "number" },
      { key: "login_max_attempts", label: "ログイン失敗許容回数（IP 単位）", type: "number" },
      {
        key: "login_username_max_attempts",
        label: "ログイン失敗許容回数（ユーザー名単位）",
        type: "number",
        help: "分散総当たり対策。IP しきい値より大きくしてください。",
      },
      { key: "login_lockout_seconds", label: "ロックアウト期間（秒）", type: "number" },
      { key: "session_max_age", label: "セッション有効期間（秒）", type: "number" },
    ],
  },
  {
    id: "voice-ai",
    title: "音声 AI チューニング",
    description: "着信 AI 応対の発話区切り（VAD）と再生タイムアウトの調整",
    icon: <MicVocal size={18} />,
    fields: [
      {
        key: "vad_mode",
        label: "VAD 積極度 (0-3)",
        type: "number",
        help: "大きいほど非音声を弾きます。変更は次の通話から反映されます。",
      },
      {
        key: "vad_min_rms",
        label: "発話とみなす最小 RMS",
        type: "number",
        help: "回線ノイズによる誤バージイン対策。0 で無効。実測: 無音ノイズ≈8、実発話は数百〜数千。",
      },
      {
        key: "playback_timeout_sec",
        label: "再生タイムアウト（秒）",
        type: "number",
        float: true,
      },
    ],
  },
  {
    id: "telephony",
    title: "電話運用",
    description: "国際発信・匿名着信・MCP・電話機管理者資格情報",
    icon: <Phone size={18} />,
    note: "「MCP サーバーを有効にする」の変更のみ core の再起動後に反映されます。",
    fields: [
      {
        key: "outbound_international_allow",
        label: "国際発信を許可するプレフィックス（カンマ区切り）",
        type: "text",
        placeholder: "01044,01081",
        help: "空の場合、国際発信はすべて拒否されます（デフォルト拒否）。保存すると FreeSWITCH 設定を再生成します。",
      },
      {
        key: "sip_reject_anonymous",
        label: "非通知（anonymous）着信を拒否する",
        type: "boolean",
        help: "注意: NTT ひかり電話 HGW 回線では caller-ID が非通知になるため、有効にすると実機着信がすべて拒否されます。",
      },
      {
        key: "mcp_default_agent_id",
        label: "MCP 既定エージェント ID",
        type: "number",
        nullable: true,
        help: "空の場合は有効な AI エージェントのうち最小 ID を使います。",
      },
      { key: "phone_admin_username", label: "電話機 Web 管理者ユーザー名", type: "text" },
      { key: "phone_admin_password", label: "電話機 Web 管理者パスワード", type: "secret" },
      { key: "mcp_enabled", label: "MCP サーバーを有効にする（要再起動）", type: "boolean" },
    ],
  },
  {
    id: "network",
    title: "ネットワーク（外向き）",
    description: "tailnet 上での管理画面 HTTPS 公開",
    icon: <Wifi size={18} />,
    fields: [
      {
        key: "tailscale_serve_enabled",
        label: "Tailscale Serve で HTTPS 公開する",
        type: "boolean",
        help: "有効にすると、次回の Tailscale 接続時に tailnet 上で管理画面を HTTPS 公開します。",
      },
    ],
  },
  {
    id: "webhooks",
    title: "Webhook",
    description: "Webhook の送信先として追加で許可する宛先",
    icon: <Webhook size={18} />,
    fields: [
      {
        key: "webhook_allowed_cidrs",
        label: "Webhook 許可 CIDR（改行またはカンマ区切り）",
        type: "textarea",
        placeholder: "172.20.0.0/16\n100.64.0.0/10",
        help: "空でも LAN（192.168.0.0/16・10.0.0.0/8）の受信サーバーには送れます。docker/子 LAN（172.16.0.0/12）・tailnet（100.64.0.0/10）宛てと、LAN 上の 80/443/2375/2376/8021 番ポート宛ては、ここに含めたときだけ許可します。millicall 自身のアドレスと loopback は常に拒否します。",
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// フォーム状態変換
// ---------------------------------------------------------------------------

/** フォーム状態: boolean はそのまま、それ以外（number 含む）は入力文字列で保持する。 */
const numberBounds: Record<string, { min: number; max?: number }> = {
  smtp_port: { min: 1, max: 65535 }, smtp_timeout: { min: 1 },
  totp_ticket_max_age: { min: 1 }, login_max_attempts: { min: 1 },
  login_username_max_attempts: { min: 1 }, login_lockout_seconds: { min: 1 },
  session_max_age: { min: 1 }, playback_timeout_sec: { min: 0 },
  vad_mode: { min: 0, max: 3 }, vad_min_rms: { min: 0 },
  saml_allowed_clock_skew_seconds: { min: 0 },
};
for (const section of SECTIONS) for (const field of section.fields) Object.assign(field, numberBounds[field.key]);
type FormState = Record<string, string | boolean>;

function settingsToForm(data: SettingsRead): FormState {
  const form: FormState = {};
  for (const section of SECTIONS) {
    for (const field of section.fields) {
      if (field.type === "secret") {
        form[field.key] = ""; // 書き込み専用（現在値は表示しない）
      } else if (field.type === "scim_role_map") {
        form[field.key] = roleMapToRowsJson(data.values[field.key]);
      } else if (field.type === "boolean") {
        form[field.key] = Boolean(data.values[field.key]);
      } else {
        const v = data.values[field.key];
        form[field.key] = v === null || v === undefined ? "" : String(v);
      }
    }
  }
  return form;
}

/** セクション内フィールドを PUT payload の values に変換する。不正な数値は Error。 */
function sectionToValues(section: SectionDef, form: FormState): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of section.fields) {
    const raw = form[field.key];
    if (field.type === "secret") {
      // 空欄 = 変更しない（書き込み専用）
      if (typeof raw === "string" && raw !== "") values[field.key] = raw;
    } else if (field.type === "scim_role_map") {
      // rows JSON → map（グループ名重複は Error → toast で通知）
      try { values[field.key] = rowsJsonToRoleMap(raw); } catch (error) { throw new FormValidationError({ [field.key]: error instanceof Error ? error.message : "割り当てを確認してください" }); }
    } else if (field.type === "boolean") {
      values[field.key] = Boolean(raw);
    } else if (field.type === "number") {
      const text = String(raw).trim();
      if (text === "") {
        if (field.nullable) values[field.key] = null;
        else throw new FormValidationError({ [field.key]: `${field.label} を入力してください` });
        continue;
      }
      const num = Number(text);
      if (!Number.isFinite(num) || (!field.float && !Number.isInteger(num))) throw new FormValidationError({ [field.key]: `${field.label} は${field.float ? "数値" : "整数"}で入力してください` });
      if (field.key === "playback_timeout_sec" && num <= 0) throw new FormValidationError({ [field.key]: "0 より大きい値を入力してください" });
      if ((field.min !== undefined && num < field.min) || (field.max !== undefined && num > field.max)) throw new FormValidationError({ [field.key]: `${field.min} ${field.max === undefined ? "以上" : `〜 ${field.max}`}の範囲で入力してください` });
      values[field.key] = num;
    } else {
      values[field.key] = String(raw);
    }
  }
  return values;
}

// ---------------------------------------------------------------------------
// ページコンポーネント
// ---------------------------------------------------------------------------

export function SettingsPage({ section: sectionMode }: { section?: "sso" } = {}) {
  const toast = useToast();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: APP_SETTINGS_KEY,
    queryFn: fetchSettings,
  });
  const data = settingsQuery.data;

  const [form, setForm] = useState<FormState>({});
  const baseline = useRef<FormState>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const title = sectionMode === "sso" ? "SSO / プロビジョニング" : "設定";
  useEffect(() => {
    if (!data) return;
    const next = settingsToForm(data);
    const previous = baseline.current;
    setForm((current) => mergeServerForm(current, previous, next));
    baseline.current = next;
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: ({ payload }: { payload: SettingsUpdate; submitted: FormState }) => updateSettings(payload),
    onSuccess: (updated, { payload, submitted }) => {
      const next = settingsToForm(updated);
      const previous = baseline.current;
      setForm((current) => acceptSavedForm(current, previous, next, submitted, [...Object.keys(payload.values ?? {}), ...(payload.reset ?? [])]));
      baseline.current = next;
      queryClient.setQueryData(APP_SETTINGS_KEY, updated);
      setFieldErrors({});
      setSaveError(null);
      toast.success("設定を保存しました");
    },
    onError: (err) => {
      if (err instanceof FormValidationError) {
        setFieldErrors(err.fieldErrors);
        requestAnimationFrame(() => document.getElementById(`setting-${Object.keys(err.fieldErrors)[0]}`)?.focus());
      }
      const message = err instanceof Error ? err.message : "保存に失敗しました";
      setSaveError(message);
      toast.error(message);
    },
  });
  const saveSection = (section: SectionDef) => {
    setFieldErrors({});
    setSaveError(null);
    try {
      saveMutation.mutate({ payload: { values: sectionToValues(section, form) }, submitted: { ...form } });
    } catch (err) {
      if (err instanceof FormValidationError) {
        setFieldErrors(err.fieldErrors);
        requestAnimationFrame(() => document.getElementById(`setting-${Object.keys(err.fieldErrors)[0]}`)?.focus());
      }
      setSaveError(err instanceof Error ? err.message : "入力値が不正です");
    }
  };
  const resetKey = (key: string) => saveMutation.mutate({ payload: { reset: [key] }, submitted: { ...form } });

  if (settingsQuery.isLoading) {
    return (
      <PageLayout title={title} description="システム全体の運用設定">
        <p className={css({ color: "text.muted", py: "6" })}>読み込み中…</p>
      </PageLayout>
    );
  }

  if (settingsQuery.isError || !data) {
    return (
      <PageLayout title={title} description="システム全体の運用設定">
        <p className={css({ color: "danger.text", py: "6" })}>
          設定の取得に失敗しました（管理者権限が必要です）。ページを再読み込みしてください。
        </p>
      </PageLayout>
    );
  }

  const overridden = new Set(data.overridden);

  return (
    <PageLayout
      title={title}
      description={sectionMode === "sso" ? "SAML ログインと SCIM によるユーザー同期を設定します" : "システム全体の運用設定。セクションごとに保存できます。"}
    >
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>
        <UnsavedChangesGuard dirty={Boolean(baseline.current && Object.keys(form).some((key) => form[key] !== baseline.current?.[key]))} busy={saveMutation.isPending} />
        {!sectionMode && <p><Link to="/sso" className={css({ color: "accent.text", textDecoration: "underline" })}>SSO / プロビジョニングの設定を開く</Link></p>}
        {saveError && <p role="alert" className={css({ color: "danger.text", fontSize: "sm" })}>{saveError}</p>}
        {SECTIONS.filter((item) => sectionMode === "sso" ? item.id === "sso" : item.id !== "sso").map((section) => (
          <SectionCard
            key={section.id}
            icon={section.icon}
            title={section.title}
            description={section.description}
          >
            {section.id === "sso" ? (
              // SAML 設定の入力補助（フォーム値に反映するのみ。保存はユーザー操作）
              <SamlSetupHelpers
                disabled={saveMutation.isPending}
                onApply={(values) => setForm((f) => ({ ...f, ...values }))}
              />
            ) : null}
            <form
              id={`settings-${section.id}-form`}
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                saveSection(section);
              }}
              className={css({ display: "flex", flexDirection: "column", gap: "4" })}
            >
              {section.note ? (
                <p
                  className={css({
                    fontSize: "sm",
                    color: "text.muted",
                    p: "2",
                    borderRadius: "md",
                    bg: "gray.50",
                    borderWidth: "1px",
                    borderStyle: "solid",
                    borderColor: "border",
                  })}
                >
                  {section.note}
                </p>
              ) : null}
              {/* 広い 2 列（狭い画面では 1 列に落とす）。詰め込みグリッドだとラベル行が折り返して崩れるため。 */}
              <div
                className={css({
                  display: "grid",
                  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                  columnGap: "6",
                  rowGap: "5",
                  alignItems: "start",
                  "@media (max-width: 860px)": { gridTemplateColumns: "1fr" },
                })}
              >
                {section.fields.map((field) => (
                  <SettingField
                    key={field.key}
                    field={field}
                    value={form[field.key] ?? (field.type === "boolean" ? false : "")}
                    isOverridden={overridden.has(field.key)}
                    secretSet={field.type === "secret" ? Boolean(data.secrets[field.key]) : false}
                    onChange={(v) => setForm((f) => ({ ...f, [field.key]: v }))}
                    onReset={() => resetKey(field.key)}
                    resetDisabled={saveMutation.isPending}
                    error={fieldErrors[field.key]}
                  />
                ))}
              </div>
              <div className={css({ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: "3", flexWrap: "wrap" })}>
                {section.fields.some((field) => form[field.key] !== baseline.current?.[field.key]) && <span className={css({ fontSize: "sm", color: "text.muted" })}>未保存の変更があります</span>}
                <button
                  type="submit"
                  className={button({ variant: "primary" })}
                  style={{ height: "36px" }}
                  disabled={saveMutation.isPending}
                >
                  {saveMutation.isPending ? "保存中…" : `${section.title} を保存`}
                </button>
              </div>
            </form>
            {section.id === "sso" ? (
              // SCIM プロビジョニング補助（テナント URL 表示 + Bearer トークン発行）
              <ScimProvisioningBlock scimEnabled={Boolean(data.values.scim_enabled)} />
            ) : null}
          </SectionCard>
        ))}
      </div>
    </PageLayout>
  );
}

// ---------------------------------------------------------------------------
// 補助コンポーネント
// ---------------------------------------------------------------------------

/** セクションカード: アイコン + タイトル + 説明 + コンテンツ（NetworkPage と同パターン）。 */
function SectionCard({
  icon,
  title,
  description,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cx(panel(), css({ p: "5", display: "flex", flexDirection: "column", gap: "4" }))}
    >
      <div
        className={css({
          display: "flex",
          alignItems: "center",
          gap: "2",
          borderBottomWidth: "1px",
          borderBottomStyle: "solid",
          borderBottomColor: "border",
          pb: "3",
        })}
      >
        <span className={css({ color: "accent" })}>{icon}</span>
        <div>
          <h2 className={css({ fontWeight: "600", fontSize: "md" })}>{title}</h2>
          <p className={css({ fontSize: "sm", color: "text.muted" })}>{description}</p>
        </div>
      </div>
      <div>{children}</div>
    </div>
  );
}

/** Label, input, help and inline error always share a stable association. */
function SettingField({ field, value, isOverridden, secretSet, onChange, onReset, resetDisabled, error }: {
  field: FieldDef; value: string | boolean; isOverridden: boolean; secretSet: boolean;
  onChange: (v: string | boolean) => void; onReset: () => void; resetDisabled: boolean; error?: string;
}) {
  const id = `setting-${field.key}`;
  const help = field.type === "secret" ? "入力した値は暗号化して保存されます。空欄のまま保存すると現在の値を保持します。" : field.help;
  const attrs = { id, "aria-invalid": Boolean(error), "aria-describedby": [help && `${id}-help`, error && `${id}-error`].filter(Boolean).join(" ") || undefined };
  return <div className={css({ minW: 0, gridColumn: field.type === "textarea" || field.type === "scim_role_map" ? "1 / -1" : undefined })}>
    <div className={css({ display: "flex", alignItems: "start", flexWrap: "wrap", gap: "2", mb: "2" })}>
      <label htmlFor={field.type === "scim_role_map" ? undefined : id} id={`${id}-label`} className={css({ fontSize: "sm", color: "text.muted", overflowWrap: "anywhere" })}>{field.label}</label>
      {isOverridden && <><span className={css({ fontSize: "sm", color: "accent.text" })}>変更済み</span><button type="button" aria-label={`${field.label}を既定に戻す`} className={cx(button({ variant: "ghost", size: "sm" }), css({ ml: "auto" }))} disabled={resetDisabled} onClick={onReset}><RotateCcw size={14} />既定に戻す</button></>}
    </div>
    {field.type === "boolean" ? <div className={css({ display: "flex", alignItems: "center", gap: "2" })}><input {...attrs} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} /><span aria-hidden="true">{value ? "有効" : "無効"}</span></div>
      : field.type === "scim_role_map" ? <div role="group" aria-labelledby={`${id}-label`}><ScimRoleMapField value={String(value)} onChange={onChange} /></div>
      : field.type === "textarea" ? <textarea {...attrs} className={cx(input(), css({ minH: "textareaMin", resize: "vertical", fontFamily: "mono" }))} value={String(value)} onChange={(e) => onChange(e.target.value)} placeholder={field.placeholder} spellCheck={false} />
      : field.type === "select" ? <select {...attrs} className={input()} value={String(value)} onChange={(e) => onChange(e.target.value)}>{field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
      : <>{field.type === "secret" && <p className={css({ display: "flex", alignItems: "center", gap: "1", fontSize: "sm", color: "text.muted", mb: "1" })}><Shield size={14} />{secretSet ? "設定済み（入力すると置き換えます）" : "未設定"}</p>}<input {...attrs} className={input()} type={field.type === "secret" ? "password" : field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step={field.float ? "any" : field.type === "number" ? 1 : undefined} required={field.type === "number" && !field.nullable} value={String(value)} onChange={(e) => onChange(e.target.value)} placeholder={field.placeholder} autoComplete={field.type === "secret" ? "new-password" : undefined} /></>}
    {help && <p id={`${id}-help`} className={css({ fontSize: "sm", color: "text.muted", mt: "1" })}>{help}</p>}
    {error && <p id={`${id}-error`} role="alert" className={css({ fontSize: "sm", color: "danger.text", mt: "1" })}>{error}</p>}
  </div>;
}

/** フィールドの補足説明。 */
function FieldHelp({ text }: { text: string }) {
  return <p className={css({ fontSize: "sm", color: "text.subtle", mt: "1" })}>{text}</p>;
}

// ---------------------------------------------------------------------------
// SSO セクション: SAML 設定の入力補助
// ---------------------------------------------------------------------------

/**
 * SAML 設定の入力補助:
 *   - IdP フェデレーションメタデータ URL からの一発取込（サーバー経由で取得・パース）
 *   - 現在アクセス中のドメインからの SP 設定自動入力（HTTPS 時のみ）
 * どちらもフォーム値に反映するだけで、保存はユーザーが内容を確認してから行う。
 */
function SamlSetupHelpers({
  disabled,
  onApply,
}: {
  disabled: boolean;
  onApply: (values: Record<string, string>) => void;
}) {
  const toast = useToast();
  const [metadataUrl, setMetadataUrl] = useState("");
  const isHttps = isHttpsProtocol(window.location.protocol);

  const importMutation = useMutation({
    mutationFn: fetchIdpMetadata,
    onSuccess: (resp) => {
      onApply(idpMetadataToFormValues(resp));
      toast.success("IdP 設定をフォームに反映しました。内容を確認して保存してください");
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "IdP メタデータの取得に失敗しました"),
  });

  const importFromUrl = () => {
    const validationError = validateMetadataUrl(metadataUrl);
    if (validationError) {
      toast.error(validationError);
      return;
    }
    importMutation.mutate(metadataUrl.trim());
  };

  const fillFromOrigin = () => {
    onApply(spValuesFromOrigin(window.location.origin));
    toast.success("SP 設定をフォームに反映しました。内容を確認して保存してください");
  };

  return (
    <div
      className={css({
        display: "flex",
        flexDirection: "column",
        gap: "4",
        mb: "4",
        p: "3",
        borderRadius: "md",
        bg: "gray.50",
        borderWidth: "1px",
        borderStyle: "solid",
        borderColor: "border",
      })}
    >
      <div>
        <label htmlFor="idp-metadata-url" className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
          IdP フェデレーションメタデータ URL から自動入力
        </label>
        <div className={css({ display: "flex", flexWrap: "wrap", gap: "2" })}>
          <input
            className={cx(input(), css({ flex: "1" }))}
            id="idp-metadata-url"
            type="url"
            value={metadataUrl}
            onChange={(e) => setMetadataUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                importFromUrl();
              }
            }}
            placeholder="https://login.microsoftonline.com/<tenant>/federationmetadata/2007-06/federationmetadata.xml?appid=<id>"
            spellCheck={false}
          />
          <button
            type="button"
            className={button({ variant: "secondary" })}
            style={{ height: "36px", flexShrink: 0 }}
            onClick={importFromUrl}
            disabled={disabled || importMutation.isPending}
          >
            <Download size={16} />
            {importMutation.isPending ? "取得中…" : "取り込み"}
          </button>
        </div>
        <FieldHelp text="Entra ID 等のメタデータ URL を貼ると、IdP Entity ID・IdP SSO URL・IdP X.509 証明書を取得してフォームに反映します（この時点では保存されません）。" />
      </div>
      <div>
        <p className={css({ fontSize: "sm", mb: "3" })}><a href="/saml/metadata" target="_blank" rel="noreferrer" className={css({ color: "accent.text", textDecoration: "underline" })}>SP メタデータを開く（別タブ）</a></p>
        <button
          type="button"
          className={button({ variant: "secondary" })}
          style={{ height: "36px" }}
          onClick={fillFromOrigin}
          disabled={disabled || !isHttps}
        >
          <Globe size={16} />
          このドメインから自動入力
        </button>
        <FieldHelp
          text={
            isHttps
              ? "SP Entity ID と ACS URL に、現在アクセス中のドメインから組み立てた値を入力します。"
              : "HTTPS でアクセス中のみ使用できます"
          }
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SSO セクション: SCIM プロビジョニング補助
// ---------------------------------------------------------------------------

/**
 * SCIM プロビジョニング補助:
 *   - テナント URL（IdP のプロビジョニング設定に入れる値）の表示 + コピー
 *   - SCIM Bearer トークンの発行 / 再発行（確認ダイアログ付き; 平文は一度だけ表示）
 */
function ScimProvisioningBlock({ scimEnabled }: { scimEnabled: boolean }) {
  const toast = useToast();
  const [token, setToken] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const tenantUrl = scimTenantUrlFromOrigin(window.location.origin);

  const rotateMutation = useMutation({
    mutationFn: rotateScimToken,
    onSuccess: (t) => {
      setToken(t);
      setConfirmOpen(false);
      toast.success("SCIM トークンを発行しました");
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : "SCIM トークンの発行に失敗しました"),
  });

  return (
    <div
      className={css({
        display: "flex",
        flexDirection: "column",
        gap: "3",
        mt: "4",
        pt: "4",
        borderTopWidth: "1px",
        borderTopStyle: "solid",
        borderTopColor: "border",
      })}
    >
      <h3 className={css({ fontSize: "sm", fontWeight: "600" })}>SCIM プロビジョニング</h3>
      <div>
        <span className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
          テナント URL
        </span>
        <CopyableValue value={tenantUrl} />
        <FieldHelp text="Entra ID 等の IdP のプロビジョニング設定で「テナント URL」に入れる値です。" />
      </div>
      {scimEnabled ? (
        <div className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
          <div>
            <button
              type="button"
              className={button({ variant: "secondary" })}
              style={{ height: "36px" }}
              onClick={() => setConfirmOpen(true)}
              disabled={rotateMutation.isPending}
            >
              <KeyRound size={16} />
              {rotateMutation.isPending ? "発行中…" : "トークンを発行 / 再発行"}
            </button>
            <FieldHelp text="再発行すると古いトークンは即座に無効になります。" />
          </div>
          {token ? (
            <div className={css({ display: "flex", flexDirection: "column", gap: "2" })}>
              <p
                className={css({
                  display: "flex",
                  alignItems: "center",
                  gap: "2",
                  fontSize: "sm",
                  color: "warn.text",
                })}
              >
                <AlertTriangle size={14} />
                このトークンはこの画面でしか表示されません。今すぐコピーして IdP に設定してください。
              </p>
              <CopyableValue value={token} />
            </div>
          ) : null}
        </div>
      ) : (
        <p className={css({ fontSize: "sm", color: "text.muted" })}>
          トークンの発行は「SCIM プロビジョニングを有効にする」をオンにして保存すると使用できます。
        </p>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title="SCIM トークンを発行しますか？"
        message="再発行すると古いトークンは即座に無効になります。IdP に設定済みのトークンは差し替えが必要です。"
        confirmLabel="発行する"
        destructive
        busy={rotateMutation.isPending}
        onConfirm={() => rotateMutation.mutate()}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}

/** 値の表示 + クリップボードコピー（SCIM テナント URL / トークン用）。 */
function CopyableValue({ value }: { value: string }) {
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
    <div className={css({ display: "flex", gap: "2", alignItems: "center" })}>
      <code
        className={css({
          flex: "1",
          fontFamily: "mono",
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
        className={button({ variant: "secondary" })}
        style={{ height: "32px", flexShrink: 0 }}
        onClick={copy}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? "コピー済み" : "コピー"}
      </button>
    </div>
  );
}
