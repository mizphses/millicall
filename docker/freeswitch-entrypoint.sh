#!/bin/sh
# ============================================================
# Phase 1 freeswitch entrypoint
# safarov/freeswitch イメージは /etc/freeswitch が空で起動するため
# vanilla 設定を先にコピーしてから FreeSWITCH を起動する。
#
# -n (no-clobber) を使う理由:
#   core が depends_on: service_healthy で先行起動し、
#   bind mount 済みファイル（directory/default.xml, sip_profiles/internal.xml,
#   dialplan/default.xml, autoload_configs/event_socket.conf.xml,
#   directory/default/）を既に生成している。
#   vanilla の cp が上書きするとホスト側の core 生成ファイルが壊れるため
#   -n で既存ファイルを保護する。
# ============================================================
set -eu

if [ ! -f /etc/freeswitch/freeswitch.xml ]; then
  echo "=== initializing vanilla freeswitch config ==="
  mkdir -p /etc/freeswitch
  # -rn: recursive かつ no-clobber — bind mount 済みの core 生成ファイルを保護
  cp -rn /usr/share/freeswitch/conf/vanilla/. /etc/freeswitch/
fi

# -nf: フォアグラウンド（フォークしない）
# -nonat: 自動NAT検出無効化（host network / 同一LAN前提）
# safarov イメージには freeswitch ユーザーが存在しないため root で起動
exec freeswitch -nf -nonat
