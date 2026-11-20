// @ts-check
// The statement pages (privacy statement, terms of use): the interface texts,
// and the text in the visitor's language (both are in the page; NL / EN
// buttons switch and remember it). Everything works without this script
// (the Dutch text is shown). The page names its title in data-title-key.
import { applyStaticI18n, getLang, setLang, t } from './core/i18n.js';

/** @type {HTMLElement|null} */
const page = document.querySelector('[data-title-key]');

function show() {
  applyStaticI18n();
  const lang = getLang();
  document.querySelectorAll('[data-lang]').forEach(el => {
    /** @type {HTMLElement} */ (el).hidden = /** @type {HTMLElement} */ (el).dataset.lang !== lang;
  });
  document.querySelectorAll('[data-setlang]').forEach(el => {
    const on = /** @type {HTMLElement} */ (el).dataset.setlang === lang;
    el.classList.toggle('active', on);
    el.setAttribute('aria-pressed', String(on));
  });
  const key = page && page.dataset.titleKey;
  if (key) document.title = `${t(key)} · ${document.title}`;
}

document.querySelectorAll('[data-setlang]').forEach(el => {
  el.addEventListener('click', () => { setLang(/** @type {HTMLElement} */ (el).dataset.setlang || 'nl'); show(); });
});
show();
