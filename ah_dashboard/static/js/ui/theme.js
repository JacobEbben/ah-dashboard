// @ts-check
// Colour theme: light (the default) or dark, chosen in the header menu. The
// system's light/dark setting is deliberately not followed. Stored per
// browser (synced when signed in): 'dark', or nothing for light.
// theme-init.js applies it early on page load; this module handles the select.
import { $ } from '../core/dom.js';
import { storage, KEYS } from '../core/storage.js';

/** @returns {'light'|'dark'} */
export function getTheme() {
  return storage.get(KEYS.theme) === 'dark' ? 'dark' : 'light';
}

/** @param {string} theme anything but 'dark' means light */
export function setTheme(theme) {
  if (theme === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
    storage.set(KEYS.theme, 'dark');
  } else {
    document.documentElement.removeAttribute('data-theme');
    storage.remove(KEYS.theme);   // light is the default: nothing to store
  }
}

export function initTheme() {
  const select = $('theme');
  select.value = getTheme();
  select.addEventListener('change', (/** @type {any} */ e) => setTheme(e.target.value));
}
