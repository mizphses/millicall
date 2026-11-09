"""SAML 2.0 SP エンドポイント（SP-initiated SSO）。

エンドポイント:
  GET  /saml/metadata  — SP メタデータ XML を返す
  GET  /saml/login     — IdP へリダイレクト（HTTP-Redirect binding）
  POST /saml/acs       — IdP からの SAMLResponse を処理する（HTTP-POST binding）

セキュリティ設計:
  - 署名検証: signxml (XMLVerifier) を使用する。xmlsec1 は不要。
  - Signature-wrapping 対策: 検証後の VerifyResult.signed_xml のみを信頼する。
    raw document から re-query しない。
  - XXE/エンティティ爆発対策: defusedxml.ElementTree で先にパースし、
    エラー時は lxml に渡さない。
  - Replay 防御: 消費済みアサーション ID / AuthnRequest ID を DB（saml_consumed_ids）に
    記録する。再起動後・複数プロセス間でも一回限りの消費を保証する（replay.py）。
  - RelayState オープンリダイレクト対策: ローカルパス（/ で始まり // でなく、
    スキームを含まない）のみ許可する。
  - 監査: saml.login.success / saml.login.failure（理由コードのみ; アサーション内容は記録しない）。

ユーザー upsert ポリシー（scim_enabled で分岐）:
  - scim_enabled=False（従来モード）:
    - メールアドレスで照合する（SAML NameID または属性）。
    - 既存ユーザーが存在する場合: origin="saml" のみ許可。display_name を更新し、
      enabled を確認する。role はそのまま維持する（既存ロールを尊重）。
    - 既存ユーザーが存在しない場合: origin="saml", role=saml_default_role,
      username=email（50 文字に切り捨て）で JIT 新規作成する。
      hashed_password はランダム Argon2 ハッシュ（ローカルログイン不可）。
  - scim_enabled=True（SCIM モード）:
    - JIT 作成は行わない。プロビジョニングは SCIM のみが担当する。
    - 照合は email → username の順（SCIM の userName は UPN であり、NameID も
      UPN のことが多い。email 未設定の SCIM ユーザーを username で救済する）。
    - origin="scim" のユーザーのみログイン許可。それ以外・未存在は 400。
    - display_name は上書きしない（SCIM が属性のソースオブトゥルース）。
    - enabled=False は拒否（SCIM の deactivate が即座に効く）。
"""

import base64
import secrets
import urllib.parse
import zlib
from datetime import UTC, datetime, timedelta

import defusedxml.ElementTree as dET
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from lxml import etree
from signxml import SignatureConfiguration, XMLVerifier
from signxml.exceptions import InvalidSignature
from sqlalchemy import select

from millicall.app_settings.service import effective_settings
from millicall.audit import get_client_ip, record_audit
from millicall.auth.csrf import generate_csrf_token
from millicall.auth.saml.replay import consume_once
from millicall.auth.security import hash_password, issue_session
from millicall.models import User

router = APIRouter(prefix="/saml", tags=["saml"])

# SAMLResponse（base64）の最大サイズ（レビュー N-4: 署名検証 DoS 防止）。1 MiB。
_MAX_SAML_RESPONSE_B64 = 1024 * 1024
_REQUEST_COOKIE = "millicall_saml_request"
_REQUEST_TTL = 600


def _request_signer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="millicall.saml.request.v1")


# SAML 名前空間定数
_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_STATUS_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"

# lxml 安全パーサー（resolve_entities=False + no_network + load_dtd=False）
# defusedxml の検証後に再パースするために使用する
_SAFE_LXML_PARSER = etree.XMLParser(
    no_network=True,
    resolve_entities=False,
    remove_comments=True,
    load_dtd=False,
)

# 名前空間文字列テンプレート（UP031 対策: f-string 化）
_NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_NS_MD = "urn:oasis:names:tc:SAML:2.0:metadata"


def _q(ns: str, tag: str) -> str:
    """Clark 記法の完全修飾タグ名を返す（"{ns}tag"）。"""
    return f"{{{ns}}}{tag}"


# ---------------------------------------------------------------------------
# XML パースユーティリティ
# ---------------------------------------------------------------------------


def _safe_parse(xml_bytes: bytes) -> etree._Element:
    """XXE/エンティティ爆発をブロックして XML をパースする。

    defusedxml.ElementTree で先に検証し、エラーなら例外を上げる。
    その後 lxml で再パースして lxml Element を返す（signxml の要件）。
    """
    # defusedxml が XXE/DTD エンティティを検出して例外を上げる
    dET.fromstring(xml_bytes)
    # defusedxml が問題なしと判定したので lxml で再パースする
    return etree.fromstring(xml_bytes, parser=_SAFE_LXML_PARSER)


# ---------------------------------------------------------------------------
# SAML ユーティリティ
# ---------------------------------------------------------------------------


def _parse_saml_datetime(dt_str: str | None) -> datetime | None:
    """SAML の ISO8601 日時文字列を UTC datetime に変換する。"""
    if not dt_str:
        return None
    try:
        s = dt_str.strip()
        # Python 3.11 以前は fromisoformat が "Z" サフィックスを受け付けない
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        parsed = datetime.fromisoformat(s)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _validate_relay_state(relay_state: str | None) -> str:
    """RelayState がローカルパスか検証する。非ローカルなら "/" を返す。

    オープンリダイレクト対策:
      - "/" で始まること
      - "//" で始まらないこと（protocol-relative URL = 外部リダイレクト）
      - スキーム（http:, https: 等）を含まないこと
    """
    if not relay_state:
        return "/"
    s = relay_state.strip()
    # 単一スラッシュ始まりのローカルパスのみ許可。以下は全て拒否（レビュー N-1 / 自動レビュー）:
    #   //evil（protocol-relative）、/\evil（バックスラッシュ→一部ブラウザで // 扱い）、
    #   バックスラッシュ全般、スキーム（://）、制御文字。
    if (
        not s.startswith("/")
        or s.startswith(("//", "/\\"))
        or "\\" in s
        or "://" in s
        or any(ord(c) < 0x20 for c in s)
    ):
        return "/"
    return s


def _build_metadata_xml(sp_entity_id: str, acs_url: str) -> str:
    """SP メタデータ XML（EntityDescriptor）を生成する。"""
    ns_map = {"md": _NS_MD, "saml": _NS_SAML}

    root = etree.Element(_q(_NS_MD, "EntityDescriptor"), nsmap=ns_map)
    root.set("entityID", sp_entity_id)

    sp_sso = etree.SubElement(root, _q(_NS_MD, "SPSSODescriptor"))
    sp_sso.set("AuthnRequestsSigned", "false")
    sp_sso.set("WantAssertionsSigned", "true")
    sp_sso.set("protocolSupportEnumeration", "urn:oasis:names:tc:SAML:2.0:protocol")

    name_id_fmt = etree.SubElement(sp_sso, _q(_NS_MD, "NameIDFormat"))
    name_id_fmt.text = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"

    acs = etree.SubElement(sp_sso, _q(_NS_MD, "AssertionConsumerService"))
    acs.set("Binding", "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    acs.set("Location", acs_url)
    acs.set("index", "1")
    acs.set("isDefault", "true")

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode()


def _build_authn_request(
    sp_entity_id: str, acs_url: str, request_id: str, issue_instant: str
) -> str:
    """SAML AuthnRequest XML を生成する（HTTP-Redirect binding 用）。"""
    root = etree.Element(
        _q(_NS_SAMLP, "AuthnRequest"),
        nsmap={"samlp": _NS_SAMLP, "saml": _NS_SAML},
    )
    root.set("ID", request_id)
    root.set("Version", "2.0")
    root.set("IssueInstant", issue_instant)
    root.set("ProtocolBinding", "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST")
    root.set("AssertionConsumerServiceURL", acs_url)

    issuer = etree.SubElement(root, _q(_NS_SAML, "Issuer"))
    issuer.text = sp_entity_id

    name_id_policy = etree.SubElement(root, _q(_NS_SAMLP, "NameIDPolicy"))
    name_id_policy.set("Format", "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress")
    name_id_policy.set("AllowCreate", "true")

    return etree.tostring(root, encoding="unicode")


def _deflate_encode(xml_str: str) -> str:
    """HTTP-Redirect binding 用に AuthnRequest を deflate+base64+urlencode する。"""
    # raw deflate: zlib.compress は 2 バイトのヘッダーと 4 バイトのチェックサムを付加する
    compressed = zlib.compress(xml_str.encode("utf-8"))[2:-4]
    b64 = base64.b64encode(compressed).decode("ascii")
    return urllib.parse.quote_plus(b64)


def _find_attribute(assertion: etree._Element, candidate_names: list[str]) -> str | None:
    """SAML Attribute から候補名でテキスト値を探す。

    Attribute/@Name の末尾部分（/区切りで最後）または @FriendlyName で一致を確認する。
    """
    for attr in assertion.findall(f".//{_q(_NS_SAML, 'Attribute')}"):
        attr_name = (attr.get("Name") or "").split("/")[-1]
        attr_fn = attr.get("FriendlyName") or ""
        if attr_name in candidate_names or attr_fn in candidate_names:
            val_elem = attr.find(_q(_NS_SAML, "AttributeValue"))
            if val_elem is not None and val_elem.text:
                return val_elem.text.strip()
    return None


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@router.get("/metadata", response_class=PlainTextResponse)
async def saml_metadata(request: Request) -> PlainTextResponse:
    """SP メタデータ XML を返す。SAML 無効時は 404（SP entity/ACS URL の露出を避ける、レビュー N-5）。"""
    settings = await effective_settings(request.app.state)
    if not settings.saml_enabled:
        raise HTTPException(status_code=404)
    xml = _build_metadata_xml(settings.saml_sp_entity_id, settings.saml_sp_acs_url)
    return PlainTextResponse(content=xml, media_type="application/xml")


@router.get("/login")
async def saml_login(request: Request, next: str = "/") -> RedirectResponse:
    """SAML SP-initiated SSO を開始する。

    SAML が無効・未設定の場合は 404 を返す。
    AuthnRequest を deflate+base64 して IdP へリダイレクトする（HTTP-Redirect binding）。
    """
    settings = await effective_settings(request.app.state)

    if not settings.saml_enabled or not settings.saml_idp_sso_url or not settings.saml_sp_entity_id:
        raise HTTPException(status_code=404, detail="SAML is not enabled")

    relay_state = _validate_relay_state(next)
    request_id = "_" + secrets.token_hex(16)
    issue_instant = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    authn_xml = _build_authn_request(
        settings.saml_sp_entity_id,
        settings.saml_sp_acs_url,
        request_id,
        issue_instant,
    )
    encoded = _deflate_encode(authn_xml)

    redirect_url = (
        settings.saml_idp_sso_url
        + ("&" if "?" in settings.saml_idp_sso_url else "?")
        + "SAMLRequest="
        + encoded
        + "&RelayState="
        + urllib.parse.quote_plus(relay_state)
    )
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        _REQUEST_COOKIE,
        _request_signer(request.app.state.secrets.session_secret).dumps(
            {"id": request_id, "next": relay_state}
        ),
        max_age=_REQUEST_TTL,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        path="/saml",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/acs")
async def saml_acs(
    request: Request,
    SAMLResponse: str = Form(...),  # noqa: N803
    RelayState: str = Form(default="/"),  # noqa: N803
) -> RedirectResponse:
    """SAML Assertion Consumer Service（ACS）。

    IdP が POST する SAMLResponse を受け取り、署名検証・条件検証・ユーザー照合・
    セッション発行を行う。

    ユーザー照合は scim_enabled で分岐する（モジュール docstring 参照）:
      - SCIM モード: origin="scim" のプロビジョニング済みユーザーのみ許可（JIT 作成なし）。
      - 従来モード: origin="saml" のみ許可、未存在なら JIT 作成する。

    失敗時は必ず 400 を返し、セッションは発行しない。
    アサーション内容はログ・監査に記録しない（理由コードのみ）。
    """
    settings = await effective_settings(request.app.state)
    secrets_state = request.app.state.secrets
    ip = get_client_ip(request)

    if not settings.saml_enabled:
        raise HTTPException(status_code=404, detail="SAML is not enabled")

    relay_state = _validate_relay_state(RelayState)

    async def _fail(reason: str, actor_label: str = "saml") -> None:
        """失敗時の監査記録ヘルパー。"""
        async with request.app.state.sessionmaker() as audit_session:
            await record_audit(
                audit_session,
                actor_user_id=None,
                actor_label=actor_label,
                action="saml.login.failure",
                detail={"reason": reason},
                ip_address=ip,
            )
            await audit_session.commit()

    # サイズ上限（レビュー N-4）: 巨大 XML への署名検証(c14n)による CPU/メモリ DoS を防ぐ。
    # 正常な SAMLResponse は数十 KB 程度なので 1 MiB を上限とする。
    if len(SAMLResponse) > _MAX_SAML_RESPONSE_B64:
        await _fail("response_too_large")
        raise HTTPException(status_code=400, detail="SAMLResponse too large")

    # ---- Step 1: base64 デコード + defusedxml + lxml パース ----
    try:
        xml_bytes = base64.b64decode(SAMLResponse)
    except Exception:  # noqa: BLE001
        await _fail("base64_decode_error")
        raise HTTPException(status_code=400, detail="Invalid SAMLResponse encoding") from None

    try:
        root = _safe_parse(xml_bytes)
    except Exception:  # noqa: BLE001
        await _fail("xml_parse_error")
        raise HTTPException(status_code=400, detail="Invalid SAMLResponse XML") from None

    # ---- Step 2: signxml による署名検証 ----
    # CRITICAL anti-signature-wrapping:
    #   signxml の VerifyResult.signed_xml が実際に署名されたサブツリーを返す。
    #   以降のアサーション属性読み取りは必ず verified_assertion からのみ行う。
    #   raw document（root）から re-query しない。
    cert_pem = settings.saml_idp_x509_cert.strip()
    if not cert_pem:
        await _fail("idp_cert_not_configured")
        raise HTTPException(status_code=400, detail="IdP certificate not configured")

    try:
        # ".//": ドキュメント内のどこでも Signature を探す。
        # これにより Assertion の内部に署名がある場合（assertion-level signing、
        # Keycloak / Entra 標準）も Response の直下にある場合（response-level signing）も対応する。
        # CRITICAL anti-wrapping: VerifyResult.signed_xml が実際に署名されたサブツリーを返すため、
        # location で見つかった Signature がどのデータを署名したかが確定し、
        # 攻撃者が別の要素を注入しても detected_xml は変わらない。
        config = SignatureConfiguration(location=".//")
        verify_result = XMLVerifier().verify(
            root,
            x509_cert=cert_pem,
            expect_config=config,
        )
    except InvalidSignature:
        await _fail("signature_invalid")
        raise HTTPException(status_code=400, detail="Signature verification failed") from None
    except Exception:  # noqa: BLE001
        await _fail("signature_error")
        raise HTTPException(status_code=400, detail="Signature verification error") from None

    # 署名済みサブツリー（以降はこれのみ信頼する）
    verified_assertion: etree._Element | None = verify_result.signed_xml
    if verified_assertion is None:
        await _fail("no_signed_xml")
        raise HTTPException(status_code=400, detail="No signed XML in assertion")

    # Accept only an Assertion or a single direct Assertion inside a signed Response.
    if verified_assertion.tag == _q(_NS_SAMLP, "Response"):
        assertions = verified_assertion.findall(_q(_NS_SAML, "Assertion"))
        if len(assertions) != 1:
            await _fail("assertion_count_invalid")
            raise HTTPException(status_code=400, detail="Expected one signed assertion")
        verified_assertion = assertions[0]
    if verified_assertion.tag != _q(_NS_SAML, "Assertion"):
        await _fail("unexpected_signed_element")
        raise HTTPException(status_code=400, detail="Expected a signed assertion")
    issuer = verified_assertion.findtext(_q(_NS_SAML, "Issuer"))
    if not settings.saml_idp_entity_id or issuer != settings.saml_idp_entity_id:
        await _fail("issuer_mismatch")
        raise HTTPException(status_code=400, detail="Issuer mismatch")

    try:
        pending = _request_signer(secrets_state.session_secret).loads(
            request.cookies.get(_REQUEST_COOKIE, ""),
            max_age=_REQUEST_TTL,
        )
        if not isinstance(pending, dict) or not pending.get("id"):
            raise ValueError("Invalid request state")
    except (BadSignature, ValueError):
        await _fail("request_state_invalid")
        raise HTTPException(status_code=400, detail="SSO must start in this browser") from None
    relay_state = _validate_relay_state(pending.get("next"))

    # ---- Step 3: Status コード検証 ----
    # Status は Response レベル属性のため root から読む（署名対象外だが情報提供のみ）
    status_code_elem = root.find(f".//{_q(_NS_SAMLP, 'StatusCode')}")
    if status_code_elem is None or status_code_elem.get("Value") != _STATUS_SUCCESS:
        await _fail("status_not_success")
        raise HTTPException(status_code=400, detail="SAML status is not Success")

    # ---- Step 4: Conditions 検証（verified_assertion からのみ読む）----
    now_utc = datetime.now(UTC)
    skew = timedelta(seconds=settings.saml_allowed_clock_skew_seconds)

    conditions_elem = verified_assertion.find(_q(_NS_SAML, "Conditions"))
    if conditions_elem is None:
        await _fail("conditions_missing")
        raise HTTPException(status_code=400, detail="Conditions element missing")

    not_before = _parse_saml_datetime(conditions_elem.get("NotBefore"))
    not_on_or_after = _parse_saml_datetime(conditions_elem.get("NotOnOrAfter"))

    if not_on_or_after is None or (
        conditions_elem.get("NotBefore") is not None and not_before is None
    ):
        await _fail("conditions_time_invalid")
        raise HTTPException(status_code=400, detail="Invalid assertion validity window")

    if not_before is not None and now_utc < not_before - skew:
        await _fail("conditions_not_yet_valid")
        raise HTTPException(status_code=400, detail="Assertion not yet valid")

    if now_utc >= not_on_or_after + skew:
        await _fail("conditions_expired")
        raise HTTPException(status_code=400, detail="Assertion has expired")

    # AudienceRestriction 検証
    audience_restrictions = conditions_elem.findall(_q(_NS_SAML, "AudienceRestriction"))
    if not audience_restrictions:
        await _fail("audience_restriction_missing")
        raise HTTPException(status_code=400, detail="AudienceRestriction missing")

    if any(
        settings.saml_sp_entity_id not in [a.text for a in ar.findall(_q(_NS_SAML, "Audience"))]
        for ar in audience_restrictions
    ):
        await _fail("audience_mismatch")
        raise HTTPException(status_code=400, detail="Audience mismatch")

    # ---- Step 5: SubjectConfirmationData 検証 ----
    subject_elem = verified_assertion.find(_q(_NS_SAML, "Subject"))
    confirmed = False
    if subject_elem is not None:
        for sc in subject_elem.findall(_q(_NS_SAML, "SubjectConfirmation")):
            if sc.get("Method") != "urn:oasis:names:tc:SAML:2.0:cm:bearer":
                continue
            sc_data = sc.find(_q(_NS_SAML, "SubjectConfirmationData"))
            if sc_data is None:
                continue
            recipient = sc_data.get("Recipient")
            if recipient != settings.saml_sp_acs_url:
                await _fail("recipient_mismatch")
                raise HTTPException(status_code=400, detail="Recipient mismatch")
            sc_noa = _parse_saml_datetime(sc_data.get("NotOnOrAfter"))
            if sc_noa is None or now_utc >= sc_noa + skew:
                await _fail("subject_confirmation_expired")
                raise HTTPException(status_code=400, detail="SubjectConfirmationData expired")
            if sc_data.get("InResponseTo") == pending["id"] and sc_data.get("NotBefore") is None:
                confirmed = True
    if not confirmed:
        await _fail("subject_confirmation_invalid")
        raise HTTPException(
            status_code=400, detail="Subject confirmation does not match login request"
        )

    # ---- Step 6: NameID / 属性抽出 ----
    name_id: str | None = None
    if subject_elem is not None:
        name_id_elem = subject_elem.find(_q(_NS_SAML, "NameID"))
        if name_id_elem is not None and name_id_elem.text:
            name_id = name_id_elem.text.strip()

    email_from_attr = _find_attribute(
        verified_assertion,
        [
            "email",
            "emailAddress",
            "mail",
            "emailaddress",  # Entra ID の小文字版
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        ],
    )
    display_name_from_attr = _find_attribute(
        verified_assertion,
        [
            "displayName",
            "name",
            "cn",
            "http://schemas.microsoft.com/identity/claims/displayname",
            "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
        ],
    )

    # 識別子（email）の決定: 属性 > NameID
    email = email_from_attr or name_id
    if not email:
        await _fail("no_identifier")
        raise HTTPException(
            status_code=400,
            detail="No identifier (NameID or email attribute) in assertion",
        )

    display_name = display_name_from_attr or email.split("@")[0]

    # ---- Step 7: Replay 防御 ----
    assertion_id = verified_assertion.get("ID") or verified_assertion.get("Id") or ""
    if not assertion_id:
        await _fail("assertion_id_missing")
        raise HTTPException(status_code=400, detail="Assertion ID missing")

    # TTL: 残有効期間 + スキュー（最低 2 * skew）
    if not_on_or_after is not None:
        remaining = (not_on_or_after - now_utc + skew).total_seconds()
        ttl = max(int(remaining), settings.saml_allowed_clock_skew_seconds * 2)
    else:
        ttl = settings.saml_allowed_clock_skew_seconds * 2

    # アサーション ID と AuthnRequest ID を同一トランザクションで一回限り消費する。
    duplicate = await consume_once(
        request.app.state.sessionmaker,
        [("assertion:" + assertion_id, ttl), ("request:" + pending["id"], _REQUEST_TTL)],
    )
    if duplicate == 0:
        await _fail("replay_detected")
        raise HTTPException(status_code=400, detail="Replay detected")
    if duplicate == 1:
        await _fail("request_replayed")
        raise HTTPException(status_code=400, detail="Login request already consumed")

    # ---- Step 8: ユーザー照合 / upsert（scim_enabled で分岐）----
    async with request.app.state.sessionmaker() as db_session:
        if settings.scim_enabled:
            # SCIM モード: JIT 作成は行わず、SCIM でプロビジョニング済みの
            # ユーザーのみログインを許可する。
            # 照合順: email → username（SCIM の userName は UPN であり、NameID も
            # UPN のことが多い。email 未設定の SCIM ユーザーを username で救済する）。
            scim_user = await db_session.scalar(select(User).where(User.email == email))
            if scim_user is None:
                scim_user = await db_session.scalar(select(User).where(User.username == email))

            if scim_user is None:
                await _fail("user_not_provisioned", actor_label=email)
                raise HTTPException(
                    status_code=400,
                    detail="このユーザーは SCIM でプロビジョニングされていません",
                )
            # セキュリティ: SCIM モードでは origin="scim" 以外（local/saml）への
            # ログインを許可しない。email 一致だけで他由来アカウントのセッションを
            # 発行できると、IdP のアサートだけで乗っ取りになる（レビュー H-1 と同旨）。
            if scim_user.origin != "scim":
                await _fail("not_scim_provisioned", actor_label=email)
                raise HTTPException(
                    status_code=400,
                    detail="このユーザーは SCIM でプロビジョニングされていません",
                )
            # SCIM の deactivate（enabled=False）を即座に反映する
            if not scim_user.enabled:
                await _fail("user_disabled", actor_label=email)
                raise HTTPException(status_code=400, detail="User account is disabled")
            # display_name は上書きしない（SCIM が属性のソースオブトゥルース）
            user = scim_user
        else:
            # 従来モード: origin="saml" のみ許可し、未存在なら JIT 作成する
            existing_user = await db_session.scalar(select(User).where(User.email == email))

            if existing_user is not None:
                # セキュリティ（レビュー H-1）: SAML でローカル(または SCIM)由来アカウントへ
                # ログインさせない。email 一致だけで origin="local" の（管理者含む）アカウントの
                # セッションを発行できると、IdP が当該 email をアサートするだけで乗っ取りになる。
                # 採用は origin="saml" のアカウントに限定する（明示リンク機構は将来対応）。
                if existing_user.origin != "saml":
                    await _fail("origin_conflict", actor_label=email)
                    raise HTTPException(
                        status_code=400,
                        detail="この email は SSO 以外のアカウントに紐づいています",
                    )
                # 既存 SAML ユーザー: enabled 確認・display_name 更新（role は維持）
                if not existing_user.enabled:
                    await _fail("user_disabled", actor_label=email)
                    raise HTTPException(status_code=400, detail="User account is disabled")
                existing_user.display_name = display_name
                db_session.add(existing_user)
                user = existing_user
            else:
                # 新規 SAML ユーザー作成
                # username は email（最大 50 文字; DB unique 制約あり）
                username = email[:50]
                # ローカルログイン不可: 使用不能なランダムパスワードをハッシュ化
                unusable_pw = hash_password(secrets.token_hex(32))
                user = User(
                    username=username,
                    hashed_password=unusable_pw,
                    display_name=display_name,
                    email=email,
                    role=settings.saml_default_role,
                    origin="saml",
                    enabled=True,
                )
                db_session.add(user)

        await db_session.flush()  # user.id を確定させる

        # ---- Step 9: セッション + CSRF Cookie 発行 ----
        session_token = issue_session(
            secrets_state.session_secret, user.id, user.session_epoch, user.hashed_password
        )

        # 監査ログ（アサーション内容は記録しない；ユーザー ID のみ）
        await record_audit(
            db_session,
            actor_user_id=user.id,
            actor_label=user.username,
            action="saml.login.success",
            ip_address=ip,
        )
        await db_session.commit()

    # Cookie を付与したリダイレクトレスポンスを構築する
    redirect_resp = RedirectResponse(url=relay_state, status_code=302)
    redirect_resp.delete_cookie(
        _REQUEST_COOKIE,
        path="/saml",
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
    )
    redirect_resp.headers["Cache-Control"] = "no-store"
    redirect_resp.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    csrf_token = generate_csrf_token()
    # CSRF Cookie（non-HttpOnly; JS から読み取れる）
    redirect_resp.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=settings.session_max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    return redirect_resp
