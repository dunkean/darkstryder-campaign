const states = {
  'not-started': 'en attente', 'loading-model': 'chargement du modèle',
  running: 'en cours', complete: 'terminé', partial: 'partiel', failed: 'interrompu',
  paused: 'en pause',
  pending: 'en attente', interrupted: 'interrompu',
};

// Poll only while the Sources view is active; ignore an old request after navigation.
export function watchSourceStatus(sources, { interval = 5000 } = {}) {
  let stopped = false, timer;
  const update = async () => {
    try {
      const response = await fetch('/api/ocr-status');
      if (!response.ok) throw new Error('Statut OCR indisponible');
      const status = await response.json();
      if (stopped) return;
      const element = document.getElementById('ocrProgress');
      if (!element) return;
      const total = status.totalPages ?? sources.reduce((n, source) => n + source.pages, 0);
      const current = sources.find(source => source.id === status.currentSource);
      element.textContent = `OCR : ${states[status.state] || status.state || 'en attente'} · ${status.completedPages ?? 0} / ${total} pages`
        + (status.engine ? ` · ${status.engine}` : '')
        + (status.state === 'running' && current ? ` · ${current.name}, page PDF ${status.currentPage ?? '…'}` : '')
        + (status.error ? ` · ${status.error}` : '');
      for (const source of sources) {
        const label = document.getElementById(`sourceStatus-${source.id}`);
        const progress = status.sources?.[source.id];
        if (label) label.textContent = progress
          ? `${states[progress.state] || progress.state || 'en attente'}${progress.completedPages != null ? ` · ${progress.completedPages} / ${source.pages}` : ''}`
          : 'en attente';
      }
    } catch (error) {
      if (!stopped) {
        const element = document.getElementById('ocrProgress');
        if (element) element.textContent = error.message;
      }
    } finally {
      if (!stopped) timer = setTimeout(update, interval);
    }
  };
  update();
  return () => { stopped = true; clearTimeout(timer); };
}
