// 页面渲染前尽早执行（内联在 <head> 里调用一次），避免切换主题时闪一下默认色
function tcpbenchApplyStoredTheme() {
  var t = localStorage.getItem('tcpbench-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', t);
}

function tcpbenchToggleTheme() {
  var cur = document.documentElement.getAttribute('data-theme') || 'dark';
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('tcpbench-theme', next);
  tcpbenchUpdateToggleLabel();
}

function tcpbenchUpdateToggleLabel() {
  var btn = document.getElementById('themeToggleBtn');
  if (!btn) return;
  var cur = document.documentElement.getAttribute('data-theme') || 'dark';
  btn.textContent = cur === 'dark' ? '☀ 浅色' : '🌙 深色';
}

document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('themeToggleBtn');
  if (btn) {
    tcpbenchUpdateToggleLabel();
    btn.addEventListener('click', tcpbenchToggleTheme);
  }
});
