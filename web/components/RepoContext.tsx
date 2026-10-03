"use client";

import { createContext, useContext } from "react";
import type { Repo } from "@/lib/types";

/** The repo whose page is open. Null until the layout has looked it up. */
export const RepoContext = createContext<Repo | null>(null);

export function useRepo(): Repo | null {
  return useContext(RepoContext);
}
