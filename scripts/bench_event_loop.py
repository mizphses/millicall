#!/usr/bin/env python
"""イベントループ遅延ベンチマーク（issue #81）。

認証の Argon2 計算・AI プロバイダ生成時の低速 DNS を同一イベントループ上で並行に流し、
ループ遅延と「音声フレーム処理」の周期ジッタを計測する。外部サービス・DB・FreeSWITCH は不要。

計測対象:
  * loop lag     : 1ms スリープの超過時間（イベントループがどれだけ遅れて戻るか）
  * frame jitter : 20ms 周期のフレーム処理タスクの、各フレームの予定時刻からの遅れ。
                   音声 WS ハンドラ（mod_audio_stream の 20ms フレームを受けて処理する）の
                   **代用**であり、実際の音声 WS・ESL を流した計測ではない。
                   ループが止まった間のフレームはスキップせず、復帰後に遅れて処理された
                   ものとして全数記録する（WS バッファに溜まったフレームの扱いに相当）。

比較する実装:
  * before : main (4eb4563) 相当。async 文脈から同期関数をそのまま呼ぶ
             （verify_password / リカバリコード 10 件の逐次 verify / registry.build_llm）。
  * after  : 本修正。hashing.verify_password_async / hashing.find_recovery_code /
             registry.build_llm_async を await する。

シナリオ:
  * idle           : 負荷なし（基準値）
  * argon2_login   : --clients 本の並行クライアントがパスワード検証（不一致）を繰り返す
  * argon2_recovery: 同上でリカバリコード検証（形式は正しいが不一致 → 10 件全部を検証）
  * slow_dns       : getaddrinfo を --dns-delay 秒ブロックする疑似 DNS に差し替え、
                     openai_compatible の LLM プロバイダ生成を繰り返す

使い方（リポジトリルートから）:
    uv run python scripts/bench_event_loop.py
    uv run python scripts/bench_event_loop.py --duration 10 --clients 8 --dns-delay 0.5
"""

import argparse
import asyncio
import os
import platform
import socket
import time
from collections.abc import Awaitable, Callable
from unittest.mock import patch

from millicall.ai import registry
from millicall.auth import hashing
from millicall.auth.security import hash_password, verify_password

_FRAME_SECONDS = 0.020
_LAG_INTERVAL = 0.001


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q / 100 * len(s)) - 1))
    return s[idx]


async def _lag_monitor(stop: asyncio.Event, samples: list[float]) -> None:
    while not stop.is_set():
        t0 = time.perf_counter()
        await asyncio.sleep(_LAG_INTERVAL)
        samples.append(time.perf_counter() - t0 - _LAG_INTERVAL)


async def _audio_frames(stop: asyncio.Event, jitter: list[float]) -> None:
    """20ms 周期のフレーム処理（音声 WS ハンドラの代用）。"""
    frame = bytes(320)  # 8kHz / 16bit / 20ms
    deadline = time.perf_counter() + _FRAME_SECONDS
    while not stop.is_set():
        delay = deadline - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        jitter.append(time.perf_counter() - deadline)
        sum(frame)  # 軽いフレーム処理（RMS 計算等）の代用
        deadline += _FRAME_SECONDS


async def _client_loop(stop: asyncio.Event, op: Callable[[], Awaitable[None]], done: list[int]):
    while not stop.is_set():
        await op()
        done[0] += 1
        await asyncio.sleep(0)


def _slow_dns(delay: float):
    def _fake(*_args, **_kwargs):
        time.sleep(delay)
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))]

    return patch("millicall.net_guard.socket.getaddrinfo", _fake)


def _make_op(scenario: str, impl: str) -> Callable[[], Awaitable[None]] | None:
    if scenario == "idle":
        return None
    if scenario == "argon2_login":
        h = hash_password("correct-password")
        if impl == "before":

            async def op() -> None:
                verify_password(h, "wrong-password")

        else:

            async def op() -> None:
                await hashing.verify_password_async(h, "wrong-password")

        return op
    if scenario == "argon2_recovery":
        stored = [hash_password(f"{i:05x}-00000-00000-00000") for i in range(10)]
        wrong = "fffff-fffff-fffff-fffff"  # 形式は正しい不一致 → 10 件すべて検証
        if impl == "before":

            async def op() -> None:
                # main の _check_recovery_code と同じ逐次同期 verify
                for h in stored:
                    hashing._verify_or_false(h, wrong)

        else:

            async def op() -> None:
                await hashing.find_recovery_code(stored, wrong)

        return op
    if scenario == "slow_dns":
        cfg = {"base_url": "https://llm.example.com/v1"}
        if impl == "before":

            async def op() -> None:
                registry.build_llm("openai_compatible", cfg, "k")

        else:

            async def op() -> None:
                await registry.build_llm_async("openai_compatible", cfg, "k")

        return op
    raise ValueError(scenario)


async def _run(scenario: str, impl: str, args) -> dict:
    stop = asyncio.Event()
    lag: list[float] = []
    jitter: list[float] = []
    done = [0]
    tasks = [
        asyncio.create_task(_lag_monitor(stop, lag)),
        asyncio.create_task(_audio_frames(stop, jitter)),
    ]
    op = _make_op(scenario, impl)
    if op is not None:
        tasks += [asyncio.create_task(_client_loop(stop, op, done)) for _ in range(args.clients)]
    await asyncio.sleep(args.duration)
    stop.set()
    await asyncio.gather(*tasks)
    return {
        "scenario": scenario,
        "impl": impl,
        "lag": lag,
        "jitter": jitter,
        "ops": done[0],
    }


def _fmt(r: dict) -> str:
    ms = 1000.0
    lag, jit = r["lag"], r["jitter"]
    return (
        f"{r['scenario']:<16} {r['impl']:<7} "
        f"{_pct(lag, 50) * ms:8.2f} {_pct(lag, 95) * ms:8.2f} {max(lag) * ms:8.2f}   "
        f"{_pct(jit, 50) * ms:8.2f} {_pct(jit, 95) * ms:8.2f} {max(jit) * ms:8.2f}   "
        f"{len(jit):6d} {r['ops']:6d}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--duration", type=float, default=5.0, help="シナリオごとの計測秒数")
    parser.add_argument("--clients", type=int, default=4, help="並行クライアント数")
    parser.add_argument("--dns-delay", type=float, default=0.2, help="疑似 DNS の遅延秒数")
    parser.add_argument(
        "--scenarios",
        default="idle,argon2_login,argon2_recovery,slow_dns",
        help="カンマ区切りのシナリオ名",
    )
    args = parser.parse_args()

    print(
        f"# python={platform.python_version()} os={platform.platform()} "
        f"cpus={os.cpu_count()} argon2_workers={hashing._ARGON2_MAX_WORKERS} "
        f"duration={args.duration}s clients={args.clients} dns_delay={args.dns_delay}s"
    )
    print(
        f"{'scenario':<16} {'impl':<7} {'lag p50':>8} {'lag p95':>8} {'lag max':>8}   "
        f"{'jit p50':>8} {'jit p95':>8} {'jit max':>8}   {'frames':>6} {'ops':>6}   (ms)"
    )
    for scenario in args.scenarios.split(","):
        impls = ["-"] if scenario == "idle" else ["before", "after"]
        for impl in impls:
            with _slow_dns(args.dns_delay):
                result = asyncio.run(_run(scenario, impl, args))
            print(_fmt(result), flush=True)


if __name__ == "__main__":
    main()
