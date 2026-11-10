"""SIP 認証情報（暗号化保存）を扱うテスト用ヘルパ。

Extension.sip_password_encrypted / Trunk.password_encrypted は SecretBox の暗号文を
保存するため、フィクスチャで行を直接作るときはこのヘルパで暗号化する。
app フィクスチャがある場合は app の master_key（data/secrets.json）で暗号化し、
設定生成・credentials API から復号できるようにする。
"""

from millicall.crypto import SecretBox

# app を起動しない単体テスト（listener を直接組むもの等）で使う固定鍵。
UNIT_MASTER_KEY = "u" * 48


def box_for(app=None) -> SecretBox:
    if app is None:
        return SecretBox(UNIT_MASTER_KEY)
    return SecretBox(app.state.secrets.master_key)


def encrypt_for_test(plaintext: str, *, app=None) -> str:
    return box_for(app).encrypt(plaintext)
