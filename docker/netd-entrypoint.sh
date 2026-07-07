#!/usr/bin/env bash
# ============================================================
# millicall-netd コンテナ エントリポイント
#
# 起動順序:
#   1. 必要なディレクトリを作成する
#   2. tailscaled をバックグラウンドで起動する
#   3. dnsmasq をバックグラウンドで起動する
#   4. millicall.netd デーモンをフォアグラウンドで起動する (PID 管理)
#
# 注意事項:
#   - tailscale up はここでは実行しない。netd が "tailscale_up" コマンドで制御する。
#   - tailscaled は /dev/net/tun が利用可能な場合はカーネル TUN を使用し、
#     存在しない場合はユーザースペースネットワーキングにフォールバックする。
#     compose で devices: ["/dev/net/tun:/dev/net/tun"] を設定することを推奨する。
#   - MILLICALL_DNSMASQ_RELOAD_CMD は Dockerfile ENV で設定済み。
#     apply_dhcp コマンドは /usr/local/bin/reload-dnsmasq.sh を呼び出す。
# ============================================================
set -euo pipefail

# --- シグナルハンドラ ---
# SIGTERM/SIGINT を受信したとき、バックグラウンドプロセスも終了させる。
_stop() {
    echo "[netd-entrypoint] シグナルを受信しました。バックグラウンドプロセスを停止します。"
    if [ -n "${TAILSCALED_PID:-}" ] && kill -0 "${TAILSCALED_PID}" 2>/dev/null; then
        kill "${TAILSCALED_PID}" 2>/dev/null || true
    fi
    if [ -n "${DNSMASQ_PID:-}" ] && kill -0 "${DNSMASQ_PID}" 2>/dev/null; then
        kill "${DNSMASQ_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap _stop SIGTERM SIGINT

# --- 1. ディレクトリ作成 ---
echo "[netd-entrypoint] ディレクトリを準備します..."
# /run/millicall: UNIX ソケット (netd.sock) の置き場。core と共有 volume でマウントされる。
install -d -m 0750 /run/millicall
# dnsmasq が必要とするディレクトリ
install -d -m 0755 /etc/dnsmasq.d
install -d -m 0755 /var/lib/misc
# tailscaled の状態ファイル置き場
install -d -m 0700 /var/lib/tailscale

# --- 2. tailscaled 起動 ---
echo "[netd-entrypoint] tailscaled を起動します..."
if [ -e /dev/net/tun ]; then
    # ホストが TUN デバイスを提供している場合: カーネルネットワーキング (推奨)。
    # compose で devices: ["/dev/net/tun:/dev/net/tun"] + cap_add: [NET_ADMIN] を設定すること。
    tailscaled \
        --state=/var/lib/tailscale/tailscaled.state \
        --socket=/run/tailscale/tailscaled.sock \
        --statedir=/var/lib/tailscale \
        &
else
    # TUN デバイスが無い場合: ユーザースペースネットワーキング。
    # CAP_NET_ADMIN が不要になる代わりに、Tailscale ネットワーク経由の到達性が
    # アプリケーションレベルに限定され、LAN 向け NAT 機能は使えない。
    echo "[netd-entrypoint] 警告: /dev/net/tun が見つかりません。userspace-networking で起動します。"
    tailscaled \
        --tun=userspace-networking \
        --state=/var/lib/tailscale/tailscaled.state \
        --socket=/run/tailscale/tailscaled.sock \
        --statedir=/var/lib/tailscale \
        &
fi
TAILSCALED_PID=$!
# tailscaled がソケットを用意するまで少し待つ
sleep 1

# --- 3. dnsmasq 起動 ---
echo "[netd-entrypoint] dnsmasq を起動します..."
# 初期設定ファイルが無い場合はプレースホルダを作成して dnsmasq が起動できるようにする。
# apply_dhcp コマンドが /etc/dnsmasq.d/millicall.conf を書き込んだ後、
# reload-dnsmasq.sh が SIGHUP で設定を再読み込みさせる。
if [ ! -f /etc/dnsmasq.d/millicall.conf ]; then
    cat > /etc/dnsmasq.d/millicall.conf <<'EOF'
# millicall netd placeholder — apply_dhcp コマンドで上書きされます
# このままでは DHCP サービスは提供しません (port=0 にします)
port=0
EOF
fi

dnsmasq \
    --keep-in-foreground \
    --no-daemon \
    --log-facility=- \
    --conf-dir=/etc/dnsmasq.d \
    --pid-file=/run/dnsmasq.pid \
    &
DNSMASQ_PID=$!

# --- 4. millicall.netd デーモン起動 ---
echo "[netd-entrypoint] millicall.netd を起動します..."
# exec で PID 1 に置き換える。netd が終了するとコンテナも終了する。
exec /app/.venv/bin/python3 -m millicall.netd
