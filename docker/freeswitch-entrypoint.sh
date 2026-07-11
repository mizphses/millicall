#!/bin/sh
# ============================================================
# Phase 1 freeswitch entrypoint
# safarov/freeswitch イメージは /etc/freeswitch が空で起動するため
# vanilla 設定を先にコピーしてから FreeSWITCH を起動する。
#
# no-clobber を find + 個別コピーで自前実装する理由:
#   core が depends_on: service_healthy で先行起動し、
#   bind mount 済みファイル（directory/default.xml, sip_profiles/internal.xml,
#   dialplan/default.xml, autoload_configs/event_socket.conf.xml,
#   directory/default/）を既に生成している。vanilla の cp が上書きすると
#   ホスト側の core 生成ファイルが壊れるため、既存ファイルは保護する。
#   なお safarov イメージの cp は BusyBox 実装で、`cp -rn src/. dest/` は
#   dest（bind mount で必ず存在）を見て**エラーも出さず全体をスキップ**する
#   （GNU cp と非互換。実機で freeswitch.xml 不在の再起動ループとして発現）。
# ============================================================
set -eu

if [ ! -f /etc/freeswitch/freeswitch.xml ]; then
  echo "=== initializing vanilla freeswitch config ==="
  mkdir -p /etc/freeswitch
  cd /usr/share/freeswitch/conf/vanilla
  # ディレクトリ骨格を先に作り、ファイルは存在しないものだけコピー
  find . -type d -exec mkdir -p /etc/freeswitch/{} \;
  find . -type f | while IFS= read -r f; do
    [ -e "/etc/freeswitch/$f" ] || cp "$f" "/etc/freeswitch/$f"
  done
  cd /
  # vanilla 由来の余剰 SIP プロファイルを除去する。
  # internal.xml / external.xml は core 生成物が bind mount 済みで、
  # gateway はインライン生成（サブディレクトリ include 無し）。
  # 残すと ipv6 プロファイルの二重 bind と example.com サンプル
  # gateway (NOREG) がロードされてしまう。
  rm -f /etc/freeswitch/sip_profiles/internal-ipv6.xml \
        /etc/freeswitch/sip_profiles/external-ipv6.xml
  rm -rf /etc/freeswitch/sip_profiles/internal \
         /etc/freeswitch/sip_profiles/external \
         /etc/freeswitch/sip_profiles/external-ipv6
  # vanilla の外線プロファイル external.xml も除去する。core はトランクごとに
  # external_<name>.xml を生成し、統合 external.xml は生成しない。残すと vanilla
  # external プロファイルが sip-port 5080 を先取りして external_<先頭トランク> の
  # bind と衝突し外線が登録できなくなる（sip_profiles をディレクトリマウントにした
  # 構成では vanilla external.xml がホスト側 dir にコピーされ再発するため必須）。
  rm -f /etc/freeswitch/sip_profiles/external.xml
fi

# mod_audio_stream（イメージ同梱、AI 音声フォーク用）を自動ロードに追加する。
# vanilla の modules.conf.xml には当然含まれないため、mod_sofia の直後に挿入。
# 冪等: 既に追加済み（または将来 core が管理）ならスキップ。
MODCONF=/etc/freeswitch/autoload_configs/modules.conf.xml
if [ -f "$MODCONF" ] && ! grep -q mod_audio_stream "$MODCONF"; then
  sed -i '/<load module="mod_sofia"\/>/a\    <load module="mod_audio_stream"/>' "$MODCONF"
fi

# -nf: フォアグラウンド（フォークしない）
# -nonat: 自動NAT検出無効化（host network / 同一LAN前提）
# safarov イメージには freeswitch ユーザーが存在しないため root で起動
exec freeswitch -nf -nonat
