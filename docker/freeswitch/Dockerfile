# syntax=docker/dockerfile:1

# ============================================================
# Stage 1: builder — sofia-sip / spandsp / FreeSWITCH core+必要mod / mod_audio_stream
#   libfreeswitch-dev の apt 配布は SignalWire リポジトリ(トークン必須)にしか
#   存在しないため、ランタイムと同一バージョンの FreeSWITCH v1.10.12 を
#   ソースからビルドする(token-free)。
#
#   旧構成は runtime に safarov/freeswitch(amd64 固定 digest)を使っていたが、
#   同イメージは arm64 マニフェストを提供しないため amd64 専用だった。
#   本構成は FreeSWITCH runtime を完全にソースからビルドして safarov 依存を撤廃し、
#   linux/amd64 / linux/arm64 の両アーキでビルド可能にする(アーキ非依存)。
#
#   builder / runtime とも Debian bookworm (glibc 2.36) に揃える。
# ============================================================
FROM debian:bookworm-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive

# builder ビルド依存 (-dev)。
#   旧構成の libldns-dev は不要になった: 選択モジュールに mod_enum を含めないため
#   configure の依存チェック対象から外れる(旧構成が空 modules.conf にしていた理由が解消)。
#   libsndfile1-dev は mod_sndfile(playback / uuid_record の WAV I/O)用。
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
      libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

# FS 1.10.12 リリース時点の依存バージョンに固定。
#   spandsp: FreeSWITCH 1.10.x が公式に推奨する commit 0d2e6ac に固定する。
#   旧構成の 933d40db (2024-03) は v18 API が新しく (V18_MODE_5BIT_4545 削除・
#   v18_init 引数変更)、mod_spandsp 1.10.12 のコンパイルが
#   "'V18_MODE_5BIT_4545' undeclared" / "too few arguments to function 'v18_init'"
#   で失敗する。旧構成は mod_spandsp をビルドしていなかったため露見しなかった。
ARG SOFIA_VERSION=v1.13.17
ARG SPANDSP_COMMIT=0d2e6ac65e0e8f53d652665a743015a88bf048d4
ARG FS_VERSION=v1.10.12

# コンパイル並列度 (未指定なら nproc)。Apple Silicon の Rosetta エミュレーション
# 下では高並列で gcc がランダムに segfault するため、ローカル検証時は
# --build-arg BUILD_JOBS=4 程度に絞る。ネイティブ arm64 / amd64 (CI 実機) は
# デフォルトのままで良い。
ARG BUILD_JOBS=

# sofia-sip / spandsp: FreeSWITCH configure と mod_sofia / mod_spandsp の必須依存。
# /usr/local/lib に libsofia-sip-ua.so.0 / libspandsp.so.2 が入る。
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

# FreeSWITCH コア + 必要モジュールのみをビルド・インストールする。
#   park / echo / playback / set / sleep / transfer / bridge / answer / hangup /
#   ring_ready / start_dtmf はすべて mod_dptools 内蔵(独立 mod は不要)。
#   playback / uuid_record の実 WAV I/O は mod_sndfile / mod_native_file が担う。
#   modules.conf は bootstrap の「後」・configure の「前」に確定させる
#   (bootstrap が modules.conf を再生成しうるため)。
#
#   パスレイアウト (entrypoint / compose / HEALTHCHECK が期待する Debian 風):
#     --prefix=/usr        → freeswitch / fs_cli が /usr/bin (PATH 上)、
#                            libfreeswitch が /usr/lib
#     --sysconfdir=/etc    → 埋め込み confdir=/etc/freeswitch
#     --with-modinstdir    → モジュールを /usr/lib/freeswitch/mod に直接配置
#   make samples は呼ばない: /etc/freeswitch は空のままにし、entrypoint が
#   起動時に /usr/share/freeswitch/conf/vanilla からコピーする設計に合わせる。
RUN git clone --depth=1 --branch "${FS_VERSION}" \
      https://github.com/signalwire/freeswitch /usr/src/freeswitch \
    && cd /usr/src/freeswitch \
    && ./bootstrap.sh -j \
    && printf '%s\n' \
        'endpoints/mod_sofia' \
        'dialplans/mod_dialplan_xml' \
        'applications/mod_dptools' \
        'applications/mod_commands' \
        'event_handlers/mod_event_socket' \
        'applications/mod_spandsp' \
        'loggers/mod_console' \
        'loggers/mod_logfile' \
        'formats/mod_sndfile' \
        'formats/mod_native_file' \
        'formats/mod_tone_stream' \
      > modules.conf \
    && ./configure \
        --prefix=/usr \
        --sysconfdir=/etc \
        --with-modinstdir=/usr/lib/freeswitch/mod \
        --with-rundir=/var/run/freeswitch \
        --with-logfiledir=/var/log/freeswitch \
        --with-dbdir=/var/lib/freeswitch/db \
        --with-storagedir=/var/lib/freeswitch/storage \
        --with-recordingsdir=/var/lib/freeswitch/recordings \
        --with-soundsdir=/usr/share/freeswitch/sounds \
        --disable-libvpx --disable-libyuv \
    && make -j"${BUILD_JOBS:-$(nproc)}" \
    && make install

# vanilla マスタ設定を /usr/share/freeswitch/conf/vanilla に配置し、
# autoload_configs/modules.conf.xml を「ビルド済み mod だけを load する trim 版」に
# 差し替える。差し替えないと vanilla デフォルトが mod_conference / mod_opus /
# mod_signalwire 等の未ビルド mod を load しようとして起動時ロードエラーが多発する
# (特に mod_signalwire は libks/token 依存で本構成が撤廃したい依存)。
#   <load module="mod_sofia"/> 行は残す: entrypoint が起動時に
#   mod_audio_stream をこの直後へ sed 挿入するアンカーになっている。
RUN mkdir -p /usr/share/freeswitch/conf \
    && cp -a /usr/src/freeswitch/conf/vanilla /usr/share/freeswitch/conf/vanilla \
    && printf '%s\n' \
        '<configuration name="modules.conf" description="Modules">' \
        '  <modules>' \
        '    <load module="mod_console"/>' \
        '    <load module="mod_logfile"/>' \
        '    <load module="mod_event_socket"/>' \
        '    <load module="mod_commands"/>' \
        '    <load module="mod_dptools"/>' \
        '    <load module="mod_dialplan_xml"/>' \
        '    <load module="mod_sofia"/>' \
        '    <load module="mod_spandsp"/>' \
        '    <load module="mod_sndfile"/>' \
        '    <load module="mod_native_file"/>' \
        '    <load module="mod_tone_stream"/>' \
        '  </modules>' \
        '</configuration>' \
      > /usr/share/freeswitch/conf/vanilla/autoload_configs/modules.conf.xml \
    # pre_load_modules.conf.xml は mod_pgsql を pre-load するが、本構成は
    # SQLite コア DB を使い mod_pgsql をビルドしないため起動時に [CRIT] ロード
    # エラーになる。当該 load 行を除去する(millicall は PostgreSQL を使わない)。
    && sed -i '/<load module="mod_pgsql"\/>/d' \
        /usr/share/freeswitch/conf/vanilla/autoload_configs/pre_load_modules.conf.xml

WORKDIR /build
# libs/libwsc サブモジュールが必須 (CMakeLists add_subdirectory)
RUN git clone --recurse-submodules --depth=1 \
      https://github.com/amigniter/mod_audio_stream.git .
# out-of-source build。出力は build/mod_audio_stream.so
# (CMAKE_SHARED_LIBRARY_PREFIX "" のため lib プレフィックス無し)。
# prefix=/usr でビルドしたため freeswitch.pc は /usr/lib/pkgconfig にある。
# ENABLE_LOCAL=OFF + PKG_CONFIG_PATH で pkg-config 経由に解決させる
# (ENABLE_LOCAL=ON は /usr/local/freeswitch 前提で本構成では破綻する)。
RUN PKG_CONFIG_PATH=/usr/lib/pkgconfig \
      cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DENABLE_LOCAL=OFF \
    && cmake --build build --parallel "${BUILD_JOBS:-$(nproc)}"

# ============================================================
# Stage 2: runtime (FreeSWITCH 1.10.12, debian:bookworm-slim)
#   glibc を builder に揃え、multi-arch (linux/amd64 / linux/arm64) 対応。
# ============================================================
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# 実行時共有ライブラリのみを apt で導入する。
#   旧構成は builder から libevent を x86_64 固定パスで COPY していたが、
#   apt で入れればアーキ非依存に解決される(x86_64 ハードコード撤廃)。
#   mod_audio_stream(内蔵 libwsc)は libevent_pthreads も dlopen するため
#   libevent-pthreads-2.1-7 (別パッケージ) も必要。
RUN apt-get update && apt-get install -y --no-install-recommends \
      libssl3 \
      libcurl4 \
      libpcre3 \
      libedit2 \
      libspeex1 \
      libspeexdsp1 \
      libtiff6 \
      libsqlite3-0 \
      libevent-2.1-7 \
      libevent-pthreads-2.1-7 \
      libsndfile1 \
      libuuid1 \
      zlib1g \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# FreeSWITCH バイナリ + libfreeswitch 本体 (prefix=/usr)。
#   libfreeswitch.so* は glob で SONAME シンボリックリンク一式を拾う
#   (単一ファイル指定だと dlopen の SONAME 解決に失敗する)。
COPY --from=builder /usr/bin/freeswitch /usr/bin/freeswitch
COPY --from=builder /usr/bin/fs_cli     /usr/bin/fs_cli
COPY --from=builder /usr/lib/libfreeswitch.so* /usr/lib/

# 選択したモジュール群 + カスタム mod_audio_stream (lib プレフィックス無し)
COPY --from=builder /usr/lib/freeswitch/mod/ /usr/lib/freeswitch/mod/
COPY --from=builder /build/build/mod_audio_stream.so \
     /usr/lib/freeswitch/mod/mod_audio_stream.so

# vanilla マスタ設定。
#   FS 標準サウンド (/usr/share/freeswitch/sounds) は同梱しない: make install は
#   sounds を配置せず(別途 make sounds が必要・大容量)、millicall は TTS 生成 WAV
#   (bind mount) と tone_stream (プログラム生成) のみ使い標準プロンプトを参照しない。
COPY --from=builder /usr/share/freeswitch/conf/vanilla /usr/share/freeswitch/conf/vanilla

# sofia-sip / spandsp の実行時共有ライブラリ
COPY --from=builder /usr/local/lib/libsofia-sip-ua.so* /usr/local/lib/
COPY --from=builder /usr/local/lib/libspandsp.so*      /usr/local/lib/

# /usr/local/lib をリンカキャッシュへ登録し、実行時書込先ディレクトリを用意する。
# /etc/freeswitch は空のまま(entrypoint が起動時に vanilla からコピーする)。
RUN echo "/usr/local/lib" > /etc/ld.so.conf.d/freeswitch-local.conf \
    && ldconfig \
    && mkdir -p /etc/freeswitch /var/run/freeswitch /var/log/freeswitch \
                /var/lib/freeswitch/db /var/lib/freeswitch/storage \
                /var/lib/freeswitch/recordings /usr/share/freeswitch/sounds

# 全モジュール + freeswitch 本体の共有ライブラリ依存がビルド時に解決できることを
# アーキ非依存(ldd)で検証する。旧構成の /lib64/ld-linux-x86-64.so.2 --list は
# amd64 固定だったため ldd に置換した。
RUN set -e; fail=0; \
    for so in /usr/lib/freeswitch/mod/mod_*.so; do \
      if ldd "$so" 2>&1 | grep -q "not found"; then \
        echo "MISSING DEPS in $so:"; ldd "$so" 2>&1 | grep "not found"; fail=1; \
      fi; \
    done; \
    if ldd /usr/bin/freeswitch 2>&1 | grep -q "not found"; then \
      echo "freeswitch bin missing deps:"; ldd /usr/bin/freeswitch | grep "not found"; fail=1; \
    fi; \
    [ "$fail" = 0 ]

# vanilla-init entrypoint をイメージに同梱する。
# これにより本番ホストは compose + .env + data のみで完結し、
# リポジトリの entrypoint スクリプトを bind mount する必要が無い。
COPY docker/freeswitch-entrypoint.sh /usr/local/bin/fs-entrypoint.sh
RUN chmod +x /usr/local/bin/fs-entrypoint.sh

# core が event_socket のパスワードをランダム生成する本構成では、
# 実際の設定ファイルからパスワードを読んで status を確認する。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD sh -c 'fs_cli -p "$(sed -n "s/.*name=\"password\" value=\"\([^\"]*\)\".*/\1/p" /etc/freeswitch/autoload_configs/event_socket.conf.xml)" -x status | grep -q "is ready"'

ENTRYPOINT ["/bin/sh", "/usr/local/bin/fs-entrypoint.sh"]
