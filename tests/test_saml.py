"""SAML 2.0 SP テスト（Phase 6 Task 4）。

テスト用 IdP 鍵ペア・証明書は各テストモジュール内で生成する。
実際の署名は signxml を使用する（スタブしない）。
"""

import base64
import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from httpx import ASGITransport, AsyncClient
from lxml import etree
from signxml import XMLSigner

from millicall.config import Settings
from millicall.main import create_app
from millicall.models import User

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

_SP_ENTITY_ID = "https://sp.example.com"
_ACS_URL = "http://testserver/saml/acs"  # httpx の base_url に合わせる
_IDP_ENTITY_ID = "https://idp.example.com"
_IDP_SSO_URL = "https://idp.example.com/sso"

_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"

# ---------------------------------------------------------------------------
# フィクスチャ: IdP 鍵ペア生成（モジュール単位でキャッシュ）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def idp_keypair():
    """テスト用の自己署名 IdP 鍵ペアを生成する。"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestIdP")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TestIdP")]))
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(private_key, hashes.SHA256(), default_backend())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return {"key_pem": key_pem, "cert_pem": cert_pem, "cert": cert}


@pytest.fixture(scope="module")
def other_keypair():
    """SAML 検証に使用しない別の鍵ペア（wrong-cert テスト用）。"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OtherIdP")]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OtherIdP")]))
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .sign(private_key, hashes.SHA256(), default_backend())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return {"key_pem": key_pem, "cert_pem": cert_pem}


# ---------------------------------------------------------------------------
# フィクスチャ: SAML 有効化アプリ / クライアント
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def saml_app(tmp_path, idp_keypair):
    """SAML 有効・設定済みの FastAPI アプリ。"""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        saml_enabled=True,
        saml_sp_entity_id=_SP_ENTITY_ID,
        saml_sp_acs_url=_ACS_URL,
        saml_idp_entity_id=_IDP_ENTITY_ID,
        saml_idp_sso_url=_IDP_SSO_URL,
        saml_idp_x509_cert=idp_keypair["cert_pem"],
        saml_default_role="user",
        saml_allowed_clock_skew_seconds=120,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def saml_client(saml_app):
    """SAML 有効化アプリ向けの AsyncClient。"""
    transport = ASGITransport(app=saml_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def disabled_saml_app(tmp_path):
    """SAML 無効の FastAPI アプリ。"""
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        fs_config_dir=tmp_path / "fs",
        cookie_secure=False,
        esl_timeout_seconds=1.0,
        saml_enabled=False,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def disabled_client(disabled_saml_app):
    transport = ASGITransport(app=disabled_saml_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# SAML レスポンス ビルダー
# ---------------------------------------------------------------------------


def _build_saml_response(
    idp_key_pem: bytes,
    idp_cert_pem: str,
    *,
    email: str = "alice@example.com",
    sp_entity_id: str = _SP_ENTITY_ID,
    acs_url: str = _ACS_URL,
    idp_entity_id: str = _IDP_ENTITY_ID,
    status_success: bool = True,
    not_before_offset: timedelta = timedelta(minutes=-5),
    not_on_or_after_offset: timedelta = timedelta(hours=1),
    sc_not_on_or_after_offset: timedelta | None = timedelta(hours=1),
    audience: str | None = None,
    recipient: str | None = None,
    assertion_id: str | None = None,
    sign: bool = True,
    display_name: str | None = None,
) -> tuple[str, str]:
    """署名済み SAML Response の base64 と assertion_id を返す。

    アサーションを独立して構築してから署名し、Response に埋め込む。
    これにより名前空間コンテキストの変化による署名破損を防ぐ。

    Returns:
        (base64_encoded_xml, assertion_id)
    """
    if assertion_id is None:
        assertion_id = "_" + secrets.token_hex(16)
    resp_id = "_" + secrets.token_hex(16)
    now = datetime.now(UTC)
    ns_map = {"samlp": _SAMLP, "saml": _SAML}

    # ---- Response（アサーションを含まない骨格）を先に構築 ----
    resp = etree.Element(f"{{{_SAMLP}}}Response", nsmap=ns_map)
    resp.set("ID", resp_id)
    resp.set("Version", "2.0")
    resp.set("IssueInstant", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    resp.set("Destination", acs_url)

    resp_issuer = etree.SubElement(resp, f"{{{_SAML}}}Issuer")
    resp_issuer.text = idp_entity_id

    status = etree.SubElement(resp, f"{{{_SAMLP}}}Status")
    sc = etree.SubElement(status, f"{{{_SAMLP}}}StatusCode")
    sc.set(
        "Value",
        "urn:oasis:names:tc:SAML:2.0:status:Success"
        if status_success
        else "urn:oasis:names:tc:SAML:2.0:status:Responder",
    )

    # ---- アサーションを独立した要素として構築（resp に attach しない）----
    # NOTE: resp に SubElement として追加してから remove すると名前空間コンテキストが変化し、
    # 署名が破損する。独立して構築して署名後に append する。
    assertion = etree.Element(f"{{{_SAML}}}Assertion", nsmap=ns_map)
    assertion.set("ID", assertion_id)
    assertion.set("Version", "2.0")
    assertion.set("IssueInstant", now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    a_issuer = etree.SubElement(assertion, f"{{{_SAML}}}Issuer")
    a_issuer.text = idp_entity_id

    subject = etree.SubElement(assertion, f"{{{_SAML}}}Subject")
    name_id_elem = etree.SubElement(subject, f"{{{_SAML}}}NameID")
    name_id_elem.set("Format", "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress")
    name_id_elem.text = email

    sc_conf = etree.SubElement(subject, f"{{{_SAML}}}SubjectConfirmation")
    sc_conf.set("Method", "urn:oasis:names:tc:SAML:2.0:cm:bearer")
    scd = etree.SubElement(sc_conf, f"{{{_SAML}}}SubjectConfirmationData")
    actual_recipient = recipient if recipient is not None else acs_url
    scd.set("Recipient", actual_recipient)
    if sc_not_on_or_after_offset is not None:
        scd.set(
            "NotOnOrAfter",
            (now + sc_not_on_or_after_offset).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    conditions = etree.SubElement(assertion, f"{{{_SAML}}}Conditions")
    conditions.set("NotBefore", (now + not_before_offset).strftime("%Y-%m-%dT%H:%M:%SZ"))
    conditions.set("NotOnOrAfter", (now + not_on_or_after_offset).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ar = etree.SubElement(conditions, f"{{{_SAML}}}AudienceRestriction")
    aud = etree.SubElement(ar, f"{{{_SAML}}}Audience")
    aud.text = audience if audience is not None else sp_entity_id

    attr_stmt = etree.SubElement(assertion, f"{{{_SAML}}}AttributeStatement")
    attr = etree.SubElement(attr_stmt, f"{{{_SAML}}}Attribute")
    attr.set("Name", "email")
    attr_val = etree.SubElement(attr, f"{{{_SAML}}}AttributeValue")
    attr_val.text = email

    if display_name:
        dn_attr = etree.SubElement(attr_stmt, f"{{{_SAML}}}Attribute")
        dn_attr.set("Name", "displayName")
        dn_val = etree.SubElement(dn_attr, f"{{{_SAML}}}AttributeValue")
        dn_val.text = display_name

    if sign:
        # アサーションを直接署名する（enveloped signature が Assertion 内部に入る）。
        # これが Keycloak / Entra ID 等の標準的な assertion-level signing の形式。
        signer = XMLSigner()
        assertion = signer.sign(
            assertion,
            key=idp_key_pem,
            cert=idp_cert_pem,
            reference_uri=assertion_id,
        )

    resp.append(assertion)

    xml_bytes = etree.tostring(resp)
    return base64.b64encode(xml_bytes).decode(), assertion_id


# ---------------------------------------------------------------------------
# ヘルパー: ACS POST
# ---------------------------------------------------------------------------


async def _post_acs(
    client: AsyncClient,
    saml_b64: str,
    relay_state: str = "/dashboard",
    follow_redirects: bool = False,
) -> AsyncClient:
    """ACS エンドポイントに POST する。"""
    return await client.post(
        "/saml/acs",
        data={"SAMLResponse": saml_b64, "RelayState": relay_state},
        follow_redirects=follow_redirects,
    )


# ---------------------------------------------------------------------------
# テスト: GET /saml/metadata
# ---------------------------------------------------------------------------


async def test_metadata_wellformed(saml_client) -> None:
    """メタデータは WellFormed な XML で ACS URL を含む。"""
    resp = await saml_client.get("/saml/metadata")
    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    xml = etree.fromstring(resp.content)
    ns = {"md": "urn:oasis:names:tc:SAML:2.0:metadata"}
    acs_elem = xml.find(".//md:AssertionConsumerService", ns)
    assert acs_elem is not None
    assert acs_elem.get("Location") == _ACS_URL


async def test_metadata_sp_entity_id(saml_client) -> None:
    """メタデータの entityID が SP Entity ID と一致する。"""
    resp = await saml_client.get("/saml/metadata")
    xml = etree.fromstring(resp.content)
    assert xml.get("entityID") == _SP_ENTITY_ID


# ---------------------------------------------------------------------------
# テスト: GET /saml/login
# ---------------------------------------------------------------------------


async def test_saml_login_redirects_with_saml_request(saml_client) -> None:
    """SAML 有効時、/saml/login は IdP へ 302 リダイレクトし SAMLRequest を含む。"""
    resp = await saml_client.get("/saml/login", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert _IDP_SSO_URL in location
    assert "SAMLRequest=" in location


async def test_saml_login_disabled_returns_404(disabled_client) -> None:
    """SAML 無効時、/saml/login は 404 を返す。"""
    resp = await disabled_client.get("/saml/login")
    assert resp.status_code == 404


async def test_saml_login_relay_state_propagated(saml_client) -> None:
    """?next= で指定したローカルパスが RelayState として IdP URL に含まれる。"""
    resp = await saml_client.get("/saml/login?next=/settings", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "RelayState=" in location
    # %2Fsettings or /settings が含まれているか（URL エンコード後）
    assert "settings" in location


# ---------------------------------------------------------------------------
# テスト: POST /saml/acs — ハッピーパス
# ---------------------------------------------------------------------------


async def test_acs_happy_path_creates_user_and_session(saml_client, idp_keypair) -> None:
    """ハッピーパス: 正常な SAMLResponse で SAML ユーザーが作成されセッション Cookie が発行される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="alice@example.com",
    )
    resp = await _post_acs(saml_client, saml_b64, relay_state="/dashboard")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/dashboard"
    # セッション Cookie が発行されているか
    assert "millicall_session" in resp.cookies
    # CSRF Cookie も発行されているか
    assert "millicall_csrf" in resp.cookies

    # DB にユーザーが作成されていることを確認
    app = saml_client._transport.app
    async with app.state.sessionmaker() as session:
        from sqlalchemy import select as sa_select

        user = await session.scalar(sa_select(User).where(User.email == "alice@example.com"))
        assert user is not None
        assert user.origin == "saml"
        assert user.role == "user"
        assert user.enabled is True


async def test_acs_happy_path_updates_existing_user(saml_client, idp_keypair) -> None:
    """既存の SAML ユーザーが存在する場合、display_name が更新され role は維持される。"""
    from millicall.auth.security import hash_password

    email = "bob@example.com"
    app = saml_client._transport.app

    # 事前に SAML ユーザーを作成（role="admin"）
    async with app.state.sessionmaker() as session:
        user = User(
            username=email[:50],
            hashed_password=hash_password(secrets.token_hex(32)),
            display_name="Old Name",
            email=email,
            role="admin",
            origin="saml",
            enabled=True,
        )
        session.add(user)
        await session.commit()

    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=email,
        display_name="Bob Smith",
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64, relay_state="/")
    assert resp.status_code == 302
    assert "millicall_session" in resp.cookies

    # display_name が更新され role="admin" が維持されていること
    async with app.state.sessionmaker() as session:
        from sqlalchemy import select as sa_select

        user = await session.scalar(sa_select(User).where(User.email == email))
        assert user is not None
        assert user.display_name == "Bob Smith"
        assert user.role == "admin"  # role は維持


# ---------------------------------------------------------------------------
# テスト: 署名検証失敗系
# ---------------------------------------------------------------------------


async def test_acs_unsigned_rejected(saml_client, idp_keypair) -> None:
    """未署名の SAMLResponse は拒否される（400・セッションなし）。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="eve@example.com",
        sign=False,
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_tampered_signature_rejected(saml_client, idp_keypair) -> None:
    """署名後にアサーションを改ざんした場合は 400 を返す。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="good@example.com",
    )
    # base64 → XML → NameID を改ざん → 再 base64
    xml_bytes = base64.b64decode(saml_b64)
    xml_str = xml_bytes.decode("utf-8")
    tampered = xml_str.replace("good@example.com", "evil@example.com")
    saml_b64_tampered = base64.b64encode(tampered.encode("utf-8")).decode()

    resp = await _post_acs(saml_client, saml_b64_tampered)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_wrong_cert_rejected(saml_client, idp_keypair, other_keypair) -> None:
    """SP が信頼しない鍵で署名されたアサーションは拒否される。"""
    # other_keypair で署名するが、SP は idp_keypair の cert を信頼する
    saml_b64, _ = _build_saml_response(
        other_keypair["key_pem"],
        other_keypair["cert_pem"],
        email="attacker@example.com",
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


# ---------------------------------------------------------------------------
# テスト: Signature-wrapping
# ---------------------------------------------------------------------------


async def test_acs_signature_wrapping_rejected(saml_client, idp_keypair) -> None:
    """Signature-wrapping: 署名済みアサーションに悪意のある未署名アサーションを追加しても
    SP は署名済みアサーションのみを使用する（攻撃者が NameID を注入できない）。
    """
    # 正規アサーション ID で SAMLResponse を構築・署名
    legit_id = "_" + secrets.token_hex(16)
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="legit@example.com",
        assertion_id=legit_id,
    )

    # 署名済み XML に攻撃者の未署名アサーションを挿入する
    xml_bytes = base64.b64decode(saml_b64)
    root = etree.fromstring(xml_bytes)
    # 攻撃者アサーション
    attacker_assertion = etree.SubElement(root, f"{{{_SAML}}}Assertion")
    attacker_assertion.set("ID", "_attacker_" + secrets.token_hex(8))
    attacker_assertion.set("Version", "2.0")
    attacker_assertion.set("IssueInstant", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    attacker_subject = etree.SubElement(attacker_assertion, f"{{{_SAML}}}Subject")
    attacker_name_id = etree.SubElement(attacker_subject, f"{{{_SAML}}}NameID")
    attacker_name_id.text = "admin@victim.com"

    # 再エンコード
    saml_b64_wrapped = base64.b64encode(etree.tostring(root)).decode()

    # 送信: SP は legit@example.com のみ使用するか、または拒否するか
    resp = await _post_acs(saml_client, saml_b64_wrapped, relay_state="/")
    # signxml が署名済み assertion のみを verified_assertion として返すため、
    # 攻撃者の NameID は読まれない。正規アサーションが使われてログイン成功するか、
    # または署名エラーになるかのどちらか（どちらも攻撃者は admin になれない）
    if resp.status_code == 302:
        # 成功した場合: legit@example.com でログインしているはず
        app = saml_client._transport.app
        async with app.state.sessionmaker() as session:
            from sqlalchemy import select as sa_select

            legit_user = await session.scalar(
                sa_select(User).where(User.email == "legit@example.com")
            )
            evil_user = await session.scalar(
                sa_select(User).where(User.email == "admin@victim.com")
            )
            assert legit_user is not None  # 正規ユーザーが作成された
            assert evil_user is None  # 攻撃者ユーザーは作成されていない
    else:
        # 拒否された場合も OK（署名検証エラー）
        assert resp.status_code == 400
        assert "millicall_session" not in resp.cookies


# ---------------------------------------------------------------------------
# テスト: Conditions / Audience / Recipient
# ---------------------------------------------------------------------------


async def test_acs_expired_conditions_rejected(saml_client, idp_keypair) -> None:
    """期限切れのアサーション（NotOnOrAfter が過去）は拒否される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="carol@example.com",
        not_before_offset=timedelta(hours=-2),
        not_on_or_after_offset=timedelta(seconds=-300),  # 5分前に期限切れ
        sc_not_on_or_after_offset=timedelta(seconds=-300),
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_future_conditions_rejected(saml_client, idp_keypair) -> None:
    """未来のアサーション（NotBefore が未来すぎる）は拒否される。

    clock_skew=120秒に対して NotBefore を 300 秒先に設定する。
    """
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="dave@example.com",
        not_before_offset=timedelta(seconds=300),  # 5分先（skew=120秒を超える）
        not_on_or_after_offset=timedelta(hours=2),
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_wrong_audience_rejected(saml_client, idp_keypair) -> None:
    """Audience が SP Entity ID と異なる場合は拒否される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="eve@example.com",
        audience="https://other-sp.example.com",
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_wrong_recipient_rejected(saml_client, idp_keypair) -> None:
    """Recipient が ACS URL と異なる場合は拒否される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="frank@example.com",
        recipient="https://other-sp.example.com/saml/acs",
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_acs_status_not_success_rejected(saml_client, idp_keypair) -> None:
    """Status!=Success の SAMLResponse は拒否される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email="grace@example.com",
        status_success=False,
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


# ---------------------------------------------------------------------------
# テスト: 無効ユーザー
# ---------------------------------------------------------------------------


async def test_acs_disabled_user_rejected(saml_client, idp_keypair) -> None:
    """disabled=True の既存ユーザーは SAML ログインを拒否される。"""
    from millicall.auth.security import hash_password

    email = "disabled@example.com"
    app = saml_client._transport.app

    async with app.state.sessionmaker() as session:
        user = User(
            username=email[:50],
            hashed_password=hash_password(secrets.token_hex(32)),
            display_name="Disabled User",
            email=email,
            role="user",
            origin="saml",
            enabled=False,  # 無効化済み
        )
        session.add(user)
        await session.commit()

    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=email,
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


# ---------------------------------------------------------------------------
# テスト: RelayState オープンリダイレクト対策
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relay_state",
    [
        "//evil.com",
        "https://evil.com",
        "http://evil.com",
        "javascript:alert(1)",
        "/\\evil.com",  # バックスラッシュ（一部ブラウザで // 扱い、レビュー N-1）
        "/path\\evil",  # 途中のバックスラッシュ
        "/path\r\nSet-Cookie: x=1",  # 制御文字（改行）
    ],
)
async def test_acs_open_redirect_falls_back_to_root(
    saml_client, idp_keypair, relay_state: str
) -> None:
    """非ローカルな RelayState は "/" にフォールバックする。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=f"user_{secrets.token_hex(4)}@example.com",
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64, relay_state=relay_state)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_acs_local_relay_state_preserved(saml_client, idp_keypair) -> None:
    """ローカルパスの RelayState はそのまま使用される。"""
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=f"localuser_{secrets.token_hex(4)}@example.com",
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64, relay_state="/settings/profile")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/settings/profile"


# ---------------------------------------------------------------------------
# テスト: Replay 防御
# ---------------------------------------------------------------------------


async def test_acs_replay_rejected(saml_client, idp_keypair) -> None:
    """同一アサーション ID を 2 回送信すると 2 回目は拒否される。"""
    assertion_id = "_replay_test_" + secrets.token_hex(8)
    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=f"replay_{secrets.token_hex(4)}@example.com",
        assertion_id=assertion_id,
    )
    # 1 回目: 成功
    resp1 = await _post_acs(saml_client, saml_b64)
    assert resp1.status_code == 302

    # 2 回目: 同一 assertion_id → リプレイ拒否
    resp2 = await _post_acs(saml_client, saml_b64)
    assert resp2.status_code == 400
    assert "millicall_session" not in resp2.cookies


# ---------------------------------------------------------------------------
# テスト: H-1 origin バインディング（既存ローカルアカウントへの SAML 乗っ取り防止）
# ---------------------------------------------------------------------------


async def test_acs_local_origin_account_rejected(saml_client, idp_keypair) -> None:
    """email 一致でも origin='local' のアカウントには SAML ログインさせない（H-1）。"""
    from millicall.auth.security import hash_password

    email = "localadmin@example.com"
    app = saml_client._transport.app
    async with app.state.sessionmaker() as session:
        session.add(
            User(
                username="localadmin",
                hashed_password=hash_password("Admin1234!"),
                display_name="Local Admin",
                email=email,
                role="admin",
                origin="local",  # ローカルアカウント
                enabled=True,
            )
        )
        await session.commit()

    saml_b64, _ = _build_saml_response(
        idp_keypair["key_pem"],
        idp_keypair["cert_pem"],
        email=email,
        assertion_id="_" + secrets.token_hex(16),
    )
    resp = await _post_acs(saml_client, saml_b64)
    assert resp.status_code == 400
    assert "millicall_session" not in resp.cookies


async def test_metadata_disabled_returns_404(disabled_client) -> None:
    """SAML 無効時は /saml/metadata が 404（SP entity/ACS URL を露出しない、N-5）。"""
    resp = await disabled_client.get("/saml/metadata")
    assert resp.status_code == 404
