// Shared by the server layout and the client theme hook, so it can't live in
// a "use client" module: the layout needs the script's actual text.

export const THEME_STORAGE_KEY = "bertha.theme";
export const OS_LIGHT_QUERY = "(prefers-color-scheme: light)";

/**
 * Runs in <head> before the page paints (app/layout.tsx), so the first frame
 * is already in the right theme. Matches resolve() in lib/theme.ts: a stored
 * "light" or "dark", otherwise the OS setting.
 */
export const THEME_SCRIPT = `(function(){var p;try{p=localStorage.getItem("${THEME_STORAGE_KEY}")}catch(e){}if(p!=="light"&&p!=="dark"){p=window.matchMedia("${OS_LIGHT_QUERY}").matches?"light":"dark"}document.documentElement.setAttribute("data-theme",p)})()`;
