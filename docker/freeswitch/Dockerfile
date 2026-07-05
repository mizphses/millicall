# syntax=docker/dockerfile:1

# ============================================================
# Stage 1: mod_audio_stream をビルド
#   libfreeswitch-dev の apt 配布は SignalWire リポジトリ(トークン必須)にしか
#   存在しない(Debian/Ubuntu 公式アーカイブには無い。Launchpad 公開履歴ゼロ)。
#   そのためランタイムと同一バージョンの FreeSWITCH v1.10.12 をソースから
#   コアのみビルドし、そのヘッダ + libfreeswitch に対してモジュールをビルドする
#   (token-free・ヘッダ/ABI 完全一致)。
#   ランタイム (safarov/freeswitch = FS 1.10.12) は Debian bookworm ベース
#   (glibc 2.36) なので builder も bookworm に揃える。
# ============================================================
FROM debian:bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      autoconf \
      automake \
      libtool \
      libtool-bin \
      pkg-config \
      cmake \
      git \
      ca-certificates \
      libssl-dev \
      zlib1g-dev \
      libsqlite3-dev \
      libcurl4-openssl-dev \
      libpcre3-dev \
      libspeex-dev \
      libspeexdsp-dev \
      libedit-dev \
      uuid-dev \
      libtiff-dev \
      libevent-dev \
      # libldns: configure はデフォルト modules.conf で依存チェックを行うため
      # mod_enum の必須依存が無いと configure 自体が失敗する (ビルドはしない)
      libldns-dev \
    && rm -rf /var/lib/apt/lists/*

# FS 1.10.12 リリース (2024-08-02) 時点の依存バージョンに固定
ARG SOFIA_VERSION=v1.13.17
ARG SPANDSP_COMMIT=933d40db635d6b2717dc4ee9cb3f72be06b0e8ee
ARG FS_VERSION=v1.10.12

# コンパイル並列度 (未指定なら nproc)。Apple Silicon の Rosetta エミュレーション
# 下では高並列で gcc がランダムに segfault するため、ローカル検証時は
# --build-arg BUILD_JOBS=4 程度に絞る。CI (実 amd64) はデフォルトのままで良い。
ARG BUILD_JOBS=

# sofia-sip / spandsp: FreeSWITCH configure の必須依存
RUN git clone --depth=1 --branch "${SOFIA_VERSION}" \
      https://github.com/freeswitch/sofia-sip /usr/src/sofia-sip \
    && cd /usr/src/sofia-sip \
    && ./bootstrap.sh \
    && ./configure \
    && make -j"${BUILD_JOBS:-$(nproc)}" \
    && make install

RUN git clone https://github.com/freeswitch/spandsp /usr/src/spandsp \
    && cd /usr/src/spandsp \
    && git checkout "${SPANDSP_COMMIT}" \
    && ./bootstrap.sh \
    && ./configure \
    && make -j"${BUILD_JOBS:-$(nproc)}" \
    && make install \
    && ldconfig

# FreeSWITCH コア (モジュール全無効 = modules.conf を空にする) を
# /usr/local/freeswitch にインストール。モジュールビルドに必要なのは
# ヘッダ・freeswitch.pc・libfreeswitch のみ。
# 注意: modules.conf は configure の「前」に空で作ること。configure は
# modules.conf (無ければ modules.conf.in 由来) に載っている全モジュールの
# 依存ライブラリ (libldns/libks 等 apt に無いものを含む) をチェックして
# 失敗する。空にしておけば依存チェックもモジュールビルドも走らない。
RUN git clone --depth=1 --branch "${FS_VERSION}" \
      https://github.com/signalwire/freeswitch /usr/src/freeswitch \
    && cd /usr/src/freeswitch \
    && ./bootstrap.sh -j \
    && : > modules.conf \
    && ./configure --disable-libvpx --disable-libyuv \
    && : > modules.conf \
    && make -j"${BUILD_JOBS:-$(nproc)}" \
    && make install

WORKDIR /build
# libs/libwsc サブモジュールが必須 (CMakeLists add_subdirectory)
RUN git clone --recurse-submodules --depth=1 \
      https://github.com/amigniter/mod_audio_stream.git .
# out-of-source build。出力は build/mod_audio_stream.so
# (CMAKE_SHARED_LIBRARY_PREFIX "" のため lib プレフィックス無し)
# ENABLE_LOCAL=ON で /usr/local/freeswitch/lib/pkgconfig の freeswitch.pc を参照
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_LOCAL=ON \
    && cmake --build build --parallel "${BUILD_JOBS:-$(nproc)}"

# ============================================================
# Stage 2: runtime (FreeSWITCH 1.10.12, 2024-08-02, amd64 固定 digest)
#   safarov/freeswitch は arm64 マニフェストを提供しないため amd64 専用。
# ============================================================
FROM safarov/freeswitch@sha256:b31c743f4c911a19687c61e3214968f2a24f93f9d3d667cc26284192e158ffc6

COPY --from=builder /build/build/mod_audio_stream.so \
     /usr/lib/freeswitch/mod/mod_audio_stream.so

# mod_audio_stream (内蔵 libwsc) は libevent (core/pthreads 含む) に
# 動的リンクするが、ミニマルなランタイムイメージには無いので builder から
# 一式同梱する (libevent_core の同梱漏れで実機 dlopen が失敗した実績あり)。
COPY --from=builder /usr/lib/x86_64-linux-gnu/libevent*.so.7* \
     /usr/lib/x86_64-linux-gnu/

# 全依存が解決できることをビルド時に検証。
# 注意: 直接依存の .so が丸ごと欠けると ld 自体が stderr にエラーを出して
# 終了し stdout の "not found" grep をすり抜けるため、stderr も合流して
# 両パターンで fail させる。
RUN out="$(/lib64/ld-linux-x86-64.so.2 --list /usr/lib/freeswitch/mod/mod_audio_stream.so 2>&1)"; \
    echo "$out"; \
    ! printf '%s' "$out" | grep -qE "not found|error while loading"

# vanilla-init entrypoint をイメージに同梱する。
# これにより本番ホストは compose + .env + data のみで完結し、
# リポジトリの entrypoint スクリプトを bind mount する必要が無い。
COPY docker/freeswitch-entrypoint.sh /usr/local/bin/fs-entrypoint.sh
RUN chmod +x /usr/local/bin/fs-entrypoint.sh

ENTRYPOINT ["/bin/sh", "/usr/local/bin/fs-entrypoint.sh"]
