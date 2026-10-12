export const UI_COMPOSER_DOCK_COOKIE = "rhetorica_composer_dock";
export const UI_RESULT_PANEL_COOKIE = "rhetorica_result_panel_open";
export const UI_SIDEBAR_COLLAPSED_COOKIE = "rhetorica_sidebar_collapsed";
export const UI_COMPOSER_DOCK_WIDTH_COOKIE = "rhetorica_composer_dock_width";
export const UI_COMPOSER_DOCK_HEIGHT_COOKIE = "rhetorica_composer_dock_height";
export const UI_SIDEBAR_WIDTH_COOKIE = "rhetorica_sidebar_width";

const UI_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

export function writeUiCookie(name: string, value: string, maxAgeSeconds = UI_COOKIE_MAX_AGE_SECONDS): void {
  if (typeof document === "undefined") {
    return;
  }
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAgeSeconds}; SameSite=Lax`;
}