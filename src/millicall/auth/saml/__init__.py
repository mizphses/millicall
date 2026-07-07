"""SAML 2.0 SP 実装パッケージ（SP-initiated SSO, signxml 署名検証）。

設計上の注意事項:
  - 署名検証は signxml (XMLVerifier) のみを使用する。xmlsec1 は不要。
  - 署名済みサブツリーは signxml の VerifyResult.signed_xml からのみ取得する
    （signature-wrapping 対策: 検証後のサブツリーのみ信頼する）。
  - XML パースは defusedxml を用いて XXE/エンティティ爆発を防ぐ。
"""
