// Polls the parts marked data-poll while the tab is visible, and asks before the
// forms marked data-confirm are sent.
document.addEventListener('submit', (event) => {
  const message = event.target.dataset && event.target.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

for (const element of document.querySelectorAll('[data-poll]')) {
  const every = Number(element.dataset.every || 5) * 1000;
  const refresh = async () => {
    if (document.hidden) {
      return;
    }
    try {
      const response = await fetch(element.dataset.poll, { credentials: 'same-origin' });
      if (response.redirected || response.status === 401) {
        window.location = '/login';
        return;
      }
      if (response.ok) {
        element.innerHTML = await response.text();
      }
    } catch (error) {
      // Keep the last view; the next poll tries again
    }
  };
  setInterval(refresh, every);
}
