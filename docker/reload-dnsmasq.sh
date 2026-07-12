#!/usr/bin/env bash
# ============================================================
# dnsmasq インコンテナ リロードスクリプト
#
# apply_dhcp が新しい millicall.conf を書き込んだ後に呼び出される。
#
# !! 完全再起動する（SIGHUP では不十分）!!
#   dnsmasq は SIGHUP では /etc/hosts 等しか再読み込みせず、
#   dhcp-range / interface / bind-interfaces といった DHCP サーバ設定は
#   反映されない。apply_dhcp が変更するのはまさにこれらの DHCP 設定なので、
#   SIGHUP だと「設定は書き換わったが DHCP が起動しない」状態になり、
#   電話に IP を配れない（実機で発覚）。よって常にプロセスを停止して
#   新しい設定で起動し直す。
#
# 使用方法:
#   MILLICALL_DNSMASQ_RELOAD_CMD=/usr/local/bin/reload-dnsmasq.sh (Dockerfile ENV デフォルト)
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

# 既存の dnsmasq を確実に停止する（PID ファイル + 名前一致の両方で掃除し、
# 二重起動によるポート 53/67 の "Address already in use" を防ぐ）。
if [ -f "${PIDFILE}" ]; then
    PID="$(cat "${PIDFILE}" 2>/dev/null || true)"
    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        echo "[reload-dnsmasq] 既存 dnsmasq (PID=${PID}) を停止します..."
        kill "${PID}" 2>/dev/null || true
    fi
    rm -f "${PIDFILE}"
fi
# PID ファイル経由で取りこぼした dnsmasq（entrypoint 起動のプレースホルダ等）も停止する。
pkill -x dnsmasq 2>/dev/null || true

# プロセスが完全に終了しソケットが解放されるのを待つ（最大約 3 秒）。
for _ in 1 2 3 4 5 6; do
    pgrep -x dnsmasq >/dev/null 2>&1 || break
    sleep 0.5
done

_start_dnsmasq
