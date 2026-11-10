import { SettingsPage } from "./SettingsPage";

/** SAML settings and SCIM credentials share one authoritative workspace. */
export function SsoPage() {
  return <SettingsPage section="sso" />;
}
