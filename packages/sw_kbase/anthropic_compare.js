const selector = document.getElementById('article-select');
selector?.addEventListener('change', () => selector.form.requestSubmit());
const toggle = document.getElementById('toggle-raw');
toggle?.addEventListener('click', () => {
  const raw = document.getElementById('response-raw');
  const readable = document.getElementById('response-readable');
  raw.hidden = !raw.hidden;
  readable.hidden = !raw.hidden;
  toggle.textContent = raw.hidden ? 'Voir le brut exact' : 'Voir le JSON lisible';
});
