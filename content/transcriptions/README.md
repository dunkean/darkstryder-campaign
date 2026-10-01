# Transcriptions des sources

Les Markdown OCR sont publiés ici après conversion, par identifiant stable de livre :
`<source-id>/book.md` et `<source-id>/pages/0001.md`. Ils servent de preuves pour
construire les fiches de la base de connaissances ; ils ne remplacent pas le contenu
éditorial de `content/entities.json` ni les données de `content/campaign.json`.

`transcription.json` conserve l'identifiant, le SHA-256 du document, le moteur et les
empreintes des Markdown. `manifest.json` conserve les rapports par page et les
avertissements de qualité. Les PDF gardent leur pagination ; un document natif
forme un segment, et les classeurs sont divisés par feuille. Les formules sont
conservées comme texte, jamais évaluées.

Les dossiers `images`, `pages/*.jpg` et `structure` sont des copies physiques du
runtime, ignorées par Git. Les références d'images Markdown restent relatives.
`_resources/resources.json` référence aussi les illustrations locales indépendantes.
`index.json` relie les documents à leurs chemins originaux, copies et collections,
avec un état explicite `complete` / `pending` / `skipped`. Les exclusions demandées
par l'utilisateur sont conservées dans `config/corpus-scope.json`, avec leur motif ;
un document exclu n'est jamais présenté comme converti. Galadinium’s Fantastic
Technology est exclu car son PDF est cassé ; Galaxy Guide 1 — A New Hope et
Special Edition Sourcebook sont exclus car aucune page n'est détectable. Leurs
originaux sont conservés. Les ajouts de campagne ne sont pas
assimilés au canon. Les liens internes des anciens sites HTML peuvent encore
pointer vers leur arborescence d'origine ; les originales restent conservées.

Le traitement OCR et ses checkpoints restent dans le runtime. La publication ne
supprime rien et refuse d'écraser une transcription modifiée localement.
