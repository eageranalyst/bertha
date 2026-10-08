"use client";

import { useEffect, useState } from "react";
import { OS_LIGHT_QUERY, THEME_STORAGE_KEY } from "./themeScript";

/** What the viewer picked: follow the OS, or always light or dark. */
export type ThemePreference = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const STORAGE_KEY = THEME_STORAGE_KEY;
const OS_LIGHT = OS_LIGHT_QUERY;
const CHANGED = "bertha-theme-changed";

function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Storage blocked: follow the OS.
  }
  return "system";
}

function resolve(preference: ThemePreference): Theme {
  if (preference !== "system") return preference;
  return window.matchMedia(OS_LIGHT).matches ? "light" : "dark";
}

function apply(preference: ThemePreference) {
  document.documentElement.setAttribute("data-theme", resolve(preference));
}

/** The viewer's theme choice and the theme in effect, kept current as the OS
 *  setting changes or another tab picks a different one. */
export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [theme, setTheme] = useState<Theme>("dark");

  useEffect(() => {
    const sync = () => {
      const current = readPreference();
      apply(current);
      setPreferenceState(current);
      setTheme(resolve(current));
    };
    sync();
    const os = window.matchMedia(OS_LIGHT);
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) sync();
    };
    os.addEventListener("change", sync);
    window.addEventListener(CHANGED, sync);
    window.addEventListener("storage", onStorage);
    return () => {
      os.removeEventListener("change", sync);
      window.removeEventListener(CHANGED, sync);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  function setPreference(next: ThemePreference) {
    try {
      if (next === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Not remembered, but still applied for this page.
    }
    apply(next);
    setPreferenceState(next);
    setTheme(resolve(next));
    window.dispatchEvent(new Event(CHANGED));
  }

  return { preference, theme, setPreference };
}
