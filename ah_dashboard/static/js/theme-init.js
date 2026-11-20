// Applies the saved colour theme before the page is painted (loaded as a
// normal, blocking script in <head>, so there is no light/dark flash).
// Light is the default; dark only when the visitor chose it. The system's
// light/dark setting is deliberately NOT followed.
(function () {
  try {
    if (localStorage.getItem('ah_theme') === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
  } catch (e) { /* storage unavailable: light */ }
})();
