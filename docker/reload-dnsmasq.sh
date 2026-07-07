#!/usr/bin/env bash
# ============================================================
# dnsmasq インコンテナ リロードスクリプト
#
# apply_dhcp が新しい millicall.conf を書き込んだ後に呼び出される。
# dnsmasq は SIGHUP で設定ファイルを再読み込みする。
#
# 使用方法:
#   MILLICALL_DNSMASQ_RELOAD_CMD=/usr/local/bin/reload-dnsmasq.sh (Dockerfile ENV デフォルト)
#
# 注意:
#   PID ファイルが無い場合は dnsmasq が未起動とみなし、新規起動を試みる。
#   SIGHUP 送信に失敗した場合は dnsmasq を再起動する (フォールバック)。
# ============================================================
set -euo pipefail

PIDFILE=/run/dnsmasq.pid

_start_dnsmasq() {
    echo "[reload-dnsmasq] dnsmasq を起動します..."
    dnsmasq \
        --keep-in-foreground \
        --no-daemon \
        --log-facility=- \
        --conf-dir=/etc/dnsmasq.d \
        --pid-file="${PIDFILE}" \
        &
}

if [ -f "${PIDFILE}" ]; then
    PID="$(cat "${PIDFILE}")"
    if kill -0 "${PID}" 2>/dev/null; then
        # プロセスが生きていれば SIGHUP で設定再読み込み
        echo "[reload-dnsmasq] dnsmasq (PID=${PID}) に SIGHUP を送信します..."
        if kill -HUP "${PID}"; then
            echo "[reload-dnsmasq] 設定を再読み込みしました。"
            exit 0
        fi
        echo "[reload-dnsmasq] 警告: SIGHUP 送信に失敗しました。再起動を試みます。"
        kill "${PID}" 2>/dev/null || true
        sleep 0.5
    else
        echo "[reload-dnsmasq] 警告: PID ファイルは存在しますが、プロセスが見つかりません。再起動します。"
    fi
else
    echo "[reload-dnsmasq] PID ファイルが存在しません。dnsmasq を新規起動します。"
fi

_start_dnsmasq
