(function () {
  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function syncThemeButton() {
    var button = document.getElementById('themeToggleBtn');
    if (!button) return;
    var label = button.querySelector('span');
    if (label) label.textContent = currentTheme() === 'dark' ? '浅色' : '深色';
  }

  function toggleTheme() {
    var next = currentTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try {
      localStorage.setItem('tcpbench-theme', next);
    } catch (e) {}
    syncThemeButton();
  }

  function showCopyState(button, text) {
    var original = button.dataset.originalLabel || button.textContent.trim();
    button.dataset.originalLabel = original;
    button.textContent = text;
    window.setTimeout(function () {
      button.textContent = original;
    }, 1400);
  }

  document.addEventListener('DOMContentLoaded', function () {
    syncThemeButton();
    var themeButton = document.getElementById('themeToggleBtn');
    if (themeButton) themeButton.addEventListener('click', toggleTheme);

    var copyButton = document.getElementById('copyCommandButton');
    if (copyButton) {
      copyButton.addEventListener('click', async function () {
        var target = document.getElementById('commandText');
        if (!target) return;
        try {
          await navigator.clipboard.writeText(target.textContent.trim());
          showCopyState(copyButton, '已复制');
        } catch (e) {
          showCopyState(copyButton, '复制失败');
        }
      });
    }
  });
})();
