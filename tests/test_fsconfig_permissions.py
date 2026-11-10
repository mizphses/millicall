"""生成する FreeSWITCH 設定の権限（issue #87）。

SIP/ESL パスワードを平文で含むため、ファイルは 0600・認証情報を置くディレクトリは 0700。
ファイル単位 bind mount を壊さないよう、上書きは in-place（inode 不変）であること。
"""

import os
import stat

from millicall.telephony.fsconfig import ExtensionConfig, FreeswitchConfigWriter, TrunkConfig


def _writer(tmp_path):
    return FreeswitchConfigWriter(
        output_dir=tmp_path / "fs",
        sip_domain="192.168.1.10",
        esl_password="esl-secret-xyz",
        sip_port=5060,
    )


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _write(tmp_path):
    return _writer(tmp_path).write_all(
        [ExtensionConfig("1001", "Alice", "pw-1001")],
        [
            TrunkConfig(
                name="hgw", display_name="HGW", host="192.168.1.1", username="3", password="t"
            )
        ],
    )


def test_generated_files_are_owner_only(tmp_path) -> None:
    old_umask = os.umask(0o022)
    try:
        written = _write(tmp_path)
    finally:
        os.umask(old_umask)
    assert written
    for p in written:
        assert _mode(p) == 0o600, p
    fs = tmp_path / "fs"
    for d in (fs, fs / "directory", fs / "directory" / "default", fs / "sip_profiles"):
        assert _mode(d) == 0o700, d


def test_existing_file_is_tightened_and_inode_preserved(tmp_path) -> None:
    _write(tmp_path)
    es = tmp_path / "fs" / "autoload_configs" / "event_socket.conf.xml"
    # 旧バージョンで 0644 のまま生成されたファイルを想定
    os.chmod(es, 0o644)
    inode = es.stat().st_ino
    _write(tmp_path)
    assert _mode(es) == 0o600
    assert es.stat().st_ino == inode
    assert 'value="esl-secret-xyz"' in es.read_text(encoding="utf-8")
