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
import { useEffect, useState } from "react";
import {
  KeyRound,
  Mail,
  MicVocal,
  Phone,
  RotateCcw,
  Shield,
  ShieldCheck,
  Wifi,
} from "lucide-react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";
import { APP_SETTINGS_KEY } from "../queryKeys";
import { PageLayout } from "../components/PageLayout";
import { useToast } from "../toast/ToastProvider";

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
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? "設定値が不正です");
  }
  if (error || !data) throw new Error("設定の保存に失敗しました");
  return data as SettingsRead;
}

// ---------------------------------------------------------------------------
// フィールド / セクション定義
// ---------------------------------------------------------------------------

type FieldDef = {
  key: string;
  label: string;
  type: "text" | "number" | "boolean" | "secret" | "textarea" | "select";
  options?: { value: string; label: string }[];
  placeholder?: string;
  /** 入力欄の下に出す補足説明。 */
  help?: string;
  /** number 入力を小数として解釈する（playback_timeout_sec 用）。 */
  float?: boolean;
  /** 空欄を null として送る（mcp_default_agent_id 用）。 */
  nullable?: boolean;
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
];

// ---------------------------------------------------------------------------
// フォーム状態変換
// ---------------------------------------------------------------------------

/** フォーム状態: boolean はそのまま、それ以外（number 含む）は入力文字列で保持する。 */
type FormState = Record<string, string | boolean>;

function settingsToForm(data: SettingsRead): FormState {
  const form: FormState = {};
  for (const section of SECTIONS) {
    for (const field of section.fields) {
      if (field.type === "secret") {
        form[field.key] = ""; // 書き込み専用（現在値は表示しない）
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
    } else if (field.type === "boolean") {
      values[field.key] = Boolean(raw);
    } else if (field.type === "number") {
      const text = String(raw).trim();
      if (text === "") {
        if (field.nullable) values[field.key] = null;
        // nullable でない数値の空欄は「変更しない」扱い
        continue;
      }
      const num = field.float ? Number.parseFloat(text) : Number.parseInt(text, 10);
      if (Number.isNaN(num)) throw new Error(`${field.label} は数値で入力してください`);
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

export function SettingsPage() {
  const toast = useToast();
  const queryClient = useQueryClient();

  const settingsQuery = useQuery({
    queryKey: APP_SETTINGS_KEY,
    queryFn: fetchSettings,
  });
  const data = settingsQuery.data;

  const [form, setForm] = useState<FormState>({});

  // サーバーデータのロード完了時にフォームを初期化する
  useEffect(() => {
    if (data) setForm(settingsToForm(data));
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (payload: SettingsUpdate) => updateSettings(payload),
    onSuccess: (updated) => {
      queryClient.setQueryData(APP_SETTINGS_KEY, updated);
      toast.success("設定を保存しました");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "保存に失敗しました"),
  });

  const saveSection = (section: SectionDef) => {
    try {
      saveMutation.mutate({ values: sectionToValues(section, form) });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "入力値が不正です");
    }
  };

  const resetKey = (key: string) => {
    saveMutation.mutate({ reset: [key] });
  };

  if (settingsQuery.isLoading) {
    return (
      <PageLayout title="設定" description="システム全体の運用設定">
        <p className={css({ color: "text.muted", py: "6" })}>読み込み中…</p>
      </PageLayout>
    );
  }

  if (settingsQuery.isError || !data) {
    return (
      <PageLayout title="設定" description="システム全体の運用設定">
        <p className={css({ color: "danger.text", py: "6" })}>
          設定の取得に失敗しました（管理者権限が必要です）。ページを再読み込みしてください。
        </p>
      </PageLayout>
    );
  }

  const overridden = new Set(data.overridden);

  return (
    <PageLayout
      title="設定"
      description="システム全体の運用設定（.env の値を上書きします。ここで変更した値は再起動なしで反映されます）"
    >
      <div className={css({ display: "flex", flexDirection: "column", gap: "6" })}>
        {SECTIONS.map((section) => (
          <SectionCard
            key={section.id}
            icon={section.icon}
            title={section.title}
            description={section.description}
          >
            <form
              id={`settings-${section.id}-form`}
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
                  />
                ))}
              </div>
              <div className={css({ display: "flex", justifyContent: "flex-end" })}>
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

/** 1 フィールド: ラベル + 上書きバッジ + 入力 + ヘルプ + 既定に戻す。 */
function SettingField({
  field,
  value,
  isOverridden,
  secretSet,
  onChange,
  onReset,
  resetDisabled,
}: {
  field: FieldDef;
  value: string | boolean;
  isOverridden: boolean;
  secretSet: boolean;
  onChange: (v: string | boolean) => void;
  onReset: () => void;
  resetDisabled: boolean;
}) {
  // ラベル行: ラベル + 上書きドット +（上書き中のみ）既定に戻すリンクを 1 行に収める。
  // ラベルは省略記号で切り、バッジ・リンクは flexShrink:0 + nowrap で潰れ・折り返しを防ぐ。
  const labelRow = (
    <span
      className={css({
        display: "flex",
        alignItems: "center",
        gap: "2",
        fontSize: "sm",
        color: "text.muted",
        mb: "1",
        minWidth: "0",
      })}
    >
      <span
        className={css({
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        })}
        title={field.label}
      >
        {field.label}
      </span>
      {isOverridden ? (
        <>
          <span
            className={css({
              display: "inline-flex",
              alignItems: "center",
              gap: "1",
              flexShrink: 0,
              whiteSpace: "nowrap",
              color: "accent",
            })}
            title=".env の値を DB で上書き中"
          >
            <span
              aria-hidden="true"
              className={css({
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                bg: "accent",
              })}
            />
            上書き中
          </span>
          <button
            type="button"
            onClick={onReset}
            disabled={resetDisabled}
            title=".env の既定値に戻す"
            className={css({
              display: "inline-flex",
              alignItems: "center",
              gap: "1",
              ml: "auto",
              flexShrink: 0,
              whiteSpace: "nowrap",
              fontSize: "sm",
              color: "text.muted",
              cursor: "pointer",
              _hover: { color: "accent" },
              _disabled: { opacity: 0.5, cursor: "not-allowed" },
            })}
          >
            <RotateCcw size={12} />
            既定に戻す
          </button>
        </>
      ) : null}
    </span>
  );

  if (field.type === "boolean") {
    return (
      <div>
        {labelRow}
        <label
          className={css({
            display: "flex",
            alignItems: "center",
            gap: "2",
            fontSize: "md",
            cursor: "pointer",
          })}
        >
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
          有効
        </label>
        {field.help ? <FieldHelp text={field.help} /> : null}
      </div>
    );
  }

  if (field.type === "secret") {
    return (
      <div>
        {labelRow}
        <div
          className={css({
            display: "flex",
            alignItems: "center",
            gap: "2",
            fontSize: "sm",
            color: secretSet ? "text.muted" : "text.subtle",
            mb: "1",
          })}
        >
          <Shield size={14} />
          <span>
            {secretSet ? "設定済み（新しい値を入力すると上書きされます）" : "未設定"}
          </span>
        </div>
        <input
          className={cx(input(), css({ width: "100%" }))}
          type="password"
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          placeholder={field.placeholder}
        />
        <FieldHelp text="入力した値は暗号化して保存されます。保存後は表示できません。空欄のまま保存すると既存の値を保持します。" />
      </div>
    );
  }

  if (field.type === "textarea") {
    return (
      <div className={css({ gridColumn: "1 / -1" })}>
        {labelRow}
        <textarea
          className={cx(
            input(),
            css({
              width: "100%",
              minH: "textareaMin",
              resize: "vertical",
              fontFamily: "monospace",
            }),
          )}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          spellCheck={false}
        />
        {field.help ? <FieldHelp text={field.help} /> : null}
      </div>
    );
  }

  if (field.type === "select") {
    return (
      <div>
        {labelRow}
        <select
          className={cx(input(), css({ width: "100%" }))}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {field.help ? <FieldHelp text={field.help} /> : null}
      </div>
    );
  }

  return (
    <div>
      {labelRow}
      <input
        className={cx(input(), css({ width: "100%" }))}
        type={field.type === "number" ? "number" : "text"}
        step={field.float ? "0.1" : undefined}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
      />
      {field.help ? <FieldHelp text={field.help} /> : null}
    </div>
  );
}

/** フィールドの補足説明。 */
function FieldHelp({ text }: { text: string }) {
  return <p className={css({ fontSize: "sm", color: "text.subtle", mt: "1" })}>{text}</p>;
}
