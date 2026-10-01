# Transcriptions des sources

Les Markdown OCR sont publiés ici après conversion, par identifiant stable de livre :
`<source-id>/book.md` et `<source-id>/pages/0001.md`. Ils servent de preuves pour
construire les fiches de la base de connaissances ; ils ne remplacent pas le contenu
éditorial de `content/entities.json` ni les données de `content/campaign.json`.

`transcription.json` conserve l'identifiant, le SHA-256 du PDF, le moteur et les
empreintes des Markdown. `manifest.json` conserve les rapports par page et les
avertissements de qualité. La numérotation est celle des pages PDF.

Le dossier `images` est un lien local vers les images du runtime, hors Git. Les
références Markdown restent relatives. Une nouvelle copie du projet doit recréer
ces liens avec `npm run transcripts:publish` une fois le runtime disponible.

Le traitement OCR et ses checkpoints restent dans le runtime. La publication ne
supprime rien et refuse d'écraser une transcription modifiée localement.
