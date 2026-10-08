import { redirect } from "next/navigation";

/** Models moved into Settings; old links and bookmarks land there. */
export default function ModelsMoved() {
  redirect("/settings/models");
}
