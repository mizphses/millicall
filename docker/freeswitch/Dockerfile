# syntax=docker/dockerfile:1

# ============================================================
# Stage 1: mod_audio_stream をビルド
#   libfreeswitch-dev は Debian bookworm/SignalWire(トークン必須) には無いが
#   Ubuntu 20.04(focal) universe に token-free で存在する (freeswitch 1.10.1)。
#   FreeSWITCH モジュールは dlopen 時にコアがシンボルを解決するため、
#   1.10 系内のマイナー差 (1.10.1 ヘッダ vs 1.10.12 ランタイム) は互換。
# ============================================================
FROM ubuntu:20.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      cmake \
      git \
      ca-certificates \
      pkg-config \
      libfreeswitch-dev \
      libssl-dev \
      zlib1g-dev \
      libspeexdsp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
# libs/libwsc サブモジュールが必須 (CMakeLists add_subdirectory)
RUN git clone --recurse-submodules --depth=1 \
      https://github.com/amigniter/mod_audio_stream.git .
# out-of-source build。出力は build/mod_audio_stream.so
# (CMAKE_SHARED_LIBRARY_PREFIX "" のため lib プレフィックス無し)
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --parallel "$(nproc)"

# ============================================================
# Stage 2: runtime (FreeSWITCH 1.10.12, 2024-08-02, amd64 固定 digest)
#   safarov/freeswitch は arm64 マニフェストを提供しないため amd64 専用。
# ============================================================
FROM safarov/freeswitch@sha256:b31c743f4c911a19687c61e3214968f2a24f93f9d3d667cc26284192e158ffc6

COPY --from=builder /build/build/mod_audio_stream.so \
     /usr/lib/freeswitch/mod/mod_audio_stream.so

# vanilla-init entrypoint をイメージに同梱する。
# これにより本番ホストは compose + .env + data のみで完結し、
# リポジトリの entrypoint スクリプトを bind mount する必要が無い。
COPY docker/freeswitch-entrypoint.sh /usr/local/bin/fs-entrypoint.sh
RUN chmod +x /usr/local/bin/fs-entrypoint.sh

ENTRYPOINT ["/bin/sh", "/usr/local/bin/fs-entrypoint.sh"]
