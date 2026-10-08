import { redirect } from "next/navigation";

/** Settings opens on its first tab. */
export default function SettingsIndex() {
  redirect("/settings/agent");
}
