// Applies the saved theme before first paint, so a dark-theme user never sees a white flash.
// A file rather than an inline script so the Content-Security-Policy can forbid inline scripts.
// Mirrors parseTheme/applyTheme in src/theme/theme.ts.
(function () {
  var palettes = ['cobalt', 'ostrakon', 'ledger'];
  var modes = ['light', 'dark'];
  try {
    var saved = JSON.parse(localStorage.getItem('ostraka.theme') || '{}');
    var root = document.documentElement;
    root.dataset.palette = palettes.indexOf(saved.palette) >= 0 ? saved.palette : 'cobalt';
    if (modes.indexOf(saved.mode) >= 0) root.dataset.mode = saved.mode;
  } catch (e) {
    document.documentElement.dataset.palette = 'cobalt';
  }
})();
