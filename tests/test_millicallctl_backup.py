"""millicallctl backup / restore の結合テスト（issue #87）。

実際の millicallctl（bash）を subprocess で実行する。docker はスタブに差し替える
（restore は稼働中コンテナの有無を docker compose ps -q で確認する）。
openssl / tar / bash が無い環境ではスキップする。
"""

import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

_CTL = Path(__file__).resolve().parent.parent / "millicallctl"
_PASS = "correct horse battery staple"
_MARKER = b"PLAINTEXT-DB-MARKER-7f3k"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(c) for c in ("bash", "openssl", "tar", "od")),
    reason="bash/openssl/tar/od が必要",
)


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    (h / "data").mkdir(parents=True)
    (h / "docker-compose.yml").write_text("services: {}\n")
    (h / ".env").write_text("MILLICALL_VERSION=test\n")
    (h / "data" / "secrets.json").write_text('{"master_key": "mk"}')
    (h / "data" / "millicall.db").write_bytes(b"SQLite format 3\x00" + _MARKER)
    return h


def _run(home: Path, *args: str, passphrase: str | None = _PASS, running: bool = False):
    stub_dir = home.parent / "stub"
    stub_dir.mkdir(exist_ok=True)
    docker = stub_dir / "docker"
    # `docker compose ps -q` の出力で稼働中かどうかを表現する
    docker.write_text("#!/bin/sh\n" + ("echo deadbeef\n" if running else "") + "exit 0\n")
    docker.chmod(0o755)
    env = {
        "PATH": f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home.parent),
        "MILLICALL_HOME": str(home),
        "MILLICALL_NO_UPDATE_CHECK": "1",
    }
    if passphrase is not None:
        env["MILLICALL_BACKUP_PASSPHRASE"] = passphrase
    return subprocess.run(
        ["bash", str(_CTL), *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _backups(home: Path) -> list[Path]:
    d = home / "backups"
    return sorted(d.iterdir()) if d.exists() else []


def test_backup_is_encrypted_and_owner_only(home):
    r = _run(home, "backup")
    assert r.returncode == 0, r.stderr
    files = _backups(home)
    assert len(files) == 1
    out = files[0]
    assert out.name.endswith(".tar.gz.enc")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert stat.S_IMODE((home / "backups").stat().st_mode) == 0o700
    raw = out.read_bytes()
    assert raw.startswith(b"Salted__")
    assert _MARKER not in raw
    assert b"master_key" not in raw
    # パスフレーズは出力にも出ない
    assert _PASS not in r.stdout + r.stderr


def test_backup_without_passphrase_non_interactive_fails(home):
    r = _run(home, "backup", passphrase=None)
    assert r.returncode != 0
    assert "MILLICALL_BACKUP_PASSPHRASE" in r.stderr
    assert _backups(home) == []


def test_backup_rejects_short_passphrase(home):
    r = _run(home, "backup", passphrase="short")
    assert r.returncode != 0
    assert _backups(home) == []


def test_passphrase_never_on_command_line():
    text = _CTL.read_text(encoding="utf-8")
    assert "-pass pass:" not in text
    assert "-pass env:BACKUP_PASS" in text


def test_restore_roundtrip(home):
    assert _run(home, "backup").returncode == 0
    backup = _backups(home)[0]
    # 復元前に壊す
    (home / "data" / "millicall.db").write_bytes(b"broken")
    (home / ".env").write_text("MILLICALL_VERSION=other\n")

    r = _run(home, "restore", str(backup))
    assert r.returncode == 0, r.stderr
    assert _MARKER in (home / "data" / "millicall.db").read_bytes()
    assert (home / ".env").read_text() == "MILLICALL_VERSION=test\n"
    assert stat.S_IMODE((home / "data").stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "data" / "secrets.json").stat().st_mode) == 0o600
    # 旧データは退避されている
    pre = [p for p in home.iterdir() if p.name.startswith("data.pre-restore-")]
    assert len(pre) == 1
    assert (pre[0] / "millicall.db").read_bytes() == b"broken"
    assert any(p.name.startswith(".env.pre-restore-") for p in home.iterdir())
    # 作業ディレクトリは片付いている
    assert not any(p.name.startswith(".restore.") for p in home.iterdir())


def test_restore_wrong_passphrase_keeps_data(home):
    assert _run(home, "backup").returncode == 0
    backup = _backups(home)[0]
    (home / "data" / "millicall.db").write_bytes(b"current")
    r = _run(home, "restore", str(backup), passphrase="wrong passphrase!!")
    assert r.returncode != 0
    assert "復号に失敗" in r.stderr
    assert (home / "data" / "millicall.db").read_bytes() == b"current"


def test_restore_requires_stopped_stack(home):
    assert _run(home, "backup").returncode == 0
    r = _run(home, "restore", str(_backups(home)[0]), running=True)
    assert r.returncode != 0
    assert "millicallctl down" in r.stderr


def test_restore_accepts_legacy_plain_tar_gz(home, tmp_path):
    legacy = tmp_path / "millicall-legacy.tar.gz"
    with tarfile.open(legacy, "w:gz") as tf:
        tf.add(home / "data", arcname="data")
        tf.add(home / ".env", arcname=".env")
    (home / "data" / "millicall.db").write_bytes(b"broken")
    r = _run(home, "restore", str(legacy), passphrase=None)
    assert r.returncode == 0, r.stderr
    assert "無暗号" in r.stdout
    assert _MARKER in (home / "data" / "millicall.db").read_bytes()


def test_restore_rejects_unexpected_members(home, tmp_path):
    evil = tmp_path / "evil.tar.gz"
    other = tmp_path / "x.txt"
    other.write_text("x")
    with tarfile.open(evil, "w:gz") as tf:
        tf.add(home / "data", arcname="data")
        tf.add(other, arcname="etc/cron.d/x")
    r = _run(home, "restore", str(evil), passphrase=None)
    assert r.returncode != 0
    assert "想定外のパス" in r.stderr
    assert _MARKER in (home / "data" / "millicall.db").read_bytes()


def test_restore_rejects_symlinks(home, tmp_path):
    evil = tmp_path / "link.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        tf.add(home / "data", arcname="data")
        info = tarfile.TarInfo("data/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc"
        tf.addfile(info)
    r = _run(home, "restore", str(evil), passphrase=None)
    assert r.returncode != 0
    assert "リンク" in r.stderr
