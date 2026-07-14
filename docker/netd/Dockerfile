# syntax=docker/dockerfile:1
# ============================================================
# millicall-netd: ネットワーク管理デーモン
#
# Ubuntu 24.04 (Noble) ベース。dnsmasq / nftables / Tailscale が
# ホストカーネルの名前空間 (network_mode: host + NET_ADMIN/NET_RAW cap) 上で
# 動作するため、Debian slim-python ではなく Ubuntu フルパッケージを使う。
#
# ターゲット: linux/amd64 / linux/arm64 (Ubuntu 24.04 の Tailscale 公式 apt
# リポジトリ "noble" は両アーキを提供。ubuntu:24.04 ベースもマルチアーチ)
# ============================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# --- システムパッケージ ---
# python3-pip: uv bootstrap に使用
# dnsmasq: DHCP / DNS キャッシュ
# nftables / iproute2: NAT ルール管理
# iputils-ping: 疎通確認用ユーティリティ
# curl / gnupg / ca-certificates: Tailscale apt リポジトリ取得
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        dnsmasq \
        nftables \
        iproute2 \
        iputils-ping \
        curl \
        gnupg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Tailscale 公式 apt リポジトリ (Ubuntu Noble 向け) ---
# https://tailscale.com/download/linux/ubuntu-2404
# 旧 URL の noble.noarch.gpg は 404 になったため noble.gpg を使う。
# noble.gpg は ASCII-armored 公開鍵なので gpg --dearmor でバイナリ keyring に変換する。
# curl を tee にパイプすると 404 でも空ファイルが作られ apt が署名エラーになるため、
# 鍵取得を独立したステップにして失敗時にビルドを中断させる。
RUN curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.gpg \
        | gpg --dearmor -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && printf 'deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu noble main\n' \
        > /etc/apt/sources.list.d/tailscale.list \
    && apt-get update && apt-get install -y --no-install-recommends tailscale \
    && rm -rf /var/lib/apt/lists/*

# --- dnsmasq / tailscaled の systemd 自動起動を無効化 ---
# コンテナ内では systemd が無いため、entrypoint が手動で起動する。
# update-rc.d / systemctl mask の代わりに /etc/init.d を直接 disable する。
RUN systemctl disable tailscaled 2>/dev/null || true \
    && systemctl disable dnsmasq  2>/dev/null || true

# --- uv インストール ---
RUN pip3 install --no-cache-dir --break-system-packages uv

WORKDIR /app

# --- millicall パッケージのインストール ---
# pyproject.toml / uv.lock / src をコピーして uv sync でインストールする。
# netd が参照するのは millicall.netd / millicall.network / millicall.config のみだが、
# パッケージ全体を 1 つのインストールにまとめてビルドキャッシュを活かす。
COPY pyproject.toml ./
COPY uv.lock ./
COPY src ./src

RUN uv sync --no-dev --frozen

# --- dnsmasq reload スクリプト ---
# entrypoint が起動したバックグラウンド dnsmasq プロセスに対して
# SIGHUP (設定再読み込み) を送る。apply_dhcp コマンドはこのスクリプトを
# MILLICALL_DNSMASQ_RELOAD_CMD 環境変数経由で呼び出す。
COPY docker/netd-entrypoint.sh /usr/local/bin/netd-entrypoint.sh
COPY docker/reload-dnsmasq.sh  /usr/local/bin/reload-dnsmasq.sh
RUN chmod +x /usr/local/bin/netd-entrypoint.sh \
             /usr/local/bin/reload-dnsmasq.sh

# --- 実行時環境変数 ---
# コンテナ内では systemctl が使えないため、専用のリロードスクリプトに差し替える。
# compose の environment ブロックでも同値を明示しているが、Dockerfile にも
# デフォルトとして記載しておくことでスタンドアロン実行でも機能する。
ENV MILLICALL_DNSMASQ_RELOAD_CMD=/usr/local/bin/reload-dnsmasq.sh

# netd が Listen する UNIX ソケットは /run/millicall/netd.sock (設定デフォルト値)。
# ディレクトリは entrypoint が作成する。

ENTRYPOINT ["/usr/local/bin/netd-entrypoint.sh"]
