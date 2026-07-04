#!/bin/sh
# ============================================================
# Phase 0 スパイク用 entrypoint
# 1) .env の値でテンプレートのプレースホルダを置換
# 2) 5060 を競合させる既定プロファイルを除去
# 3) FreeSWITCH をフォアグラウンド・NAT検出無効で起動
# FreeSWITCH本体はOS環境変数を直接読まないため sed で流し込む。
# ============================================================
set -eu

# 必須変数チェック（未設定なら明示エラーで停止）
: "${HGW_IP:?HGW_IP is required (.env)}"
: "${HGW_SIP_USER:?HGW_SIP_USER is required (.env)}"
: "${HGW_SIP_PASSWORD:?HGW_SIP_PASSWORD is required (.env)}"
: "${OUTBOUND_CALLERID:?OUTBOUND_CALLERID is required (.env)}"

CONF=/etc/freeswitch

# プレースホルダ置換ヘルパ（区切りに | を使い、値に / が来ても安全）
subst() {
  sed \
    -e "s|__HGW_IP__|${HGW_IP}|g" \
    -e "s|__HGW_SIP_USER__|${HGW_SIP_USER}|g" \
    -e "s|__HGW_SIP_PASSWORD__|${HGW_SIP_PASSWORD}|g" \
    -e "s|__OUTBOUND_CALLERID__|${OUTBOUND_CALLERID}|g" \
    "$1"
}

# 既定プロファイルを除去して external に 5060 を専有させる。
# vanilla では internal=5060 / external=5080 で二重起動するため、
# internal を消し external.xml 側で 5060 を bind する。
rm -f "$CONF"/sip_profiles/internal.xml \
      "$CONF"/sip_profiles/internal-ipv6.xml \
      "$CONF"/sip_profiles/external-ipv6.xml
rm -f "$CONF"/sip_profiles/external/*.xml 2>/dev/null || true

# テンプレート展開
subst /templates/external.xml         > "$CONF"/sip_profiles/external.xml
subst /templates/dialplan-public.xml  > "$CONF"/dialplan/public.xml
subst /templates/dialplan-default.xml > "$CONF"/dialplan/default.xml

# 生成結果を（パスワードだけ伏せて）ログ出力
echo "=== generated external.xml (password masked) ==="
sed -E 's|(name="password"[[:space:]]+value=")[^"]*|\1********|' \
    "$CONF"/sip_profiles/external.xml
echo "================================================"

# -nf: フォアグラウンド（フォークしない） / -nonat: 自動NAT検出を無効化（同一LAN）
exec freeswitch -u freeswitch -g freeswitch -nf -nonat
