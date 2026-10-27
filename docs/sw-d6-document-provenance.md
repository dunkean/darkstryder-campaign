# Origine de récupération et source documentaire

Chaque document conserve deux niveaux indépendants dans `document_provenance` :

| Niveau | Champs principaux |
|---|---|
| Acquisition | site/base/extraction/transcription locale, dataset, source ID, URL publique, identifiant natif, date et empreinte de réponse si disponibles |
| Contenus cités | tableau de titres, code WEG éventuel, pages citées ou pages PDF, type de contenu, citation brute et preuve |

Une page D6DB peut donc citer un livre WEG, une compilation, un autre site, ou ne rien indiquer. Le nom du site ne devient pas automatiquement un livre. Plusieurs références restent plusieurs références. Une abréviation non résolue reste telle quelle. Le bouton « Copy to Homebrew » de D6DB n'est pas une qualification de l'article.

L'extraction utilise les champs explicites des imports, les citations `Source:` et les sections bibliographiques wiki. Les correspondances au catalogue et à la liste WEG de Statbase sont exactes après normalisation typographique. Les paramètres connus du modèle wiki DarkStryder sont développés avec leur preuve et la règle de correspondance. Les liens ordinaires vers un personnage ou une planète ne deviennent pas des références bibliographiques. Aucune attribution par similarité de texte ou modèle n'est faite.

Les créations issues de la collection locale `addons` sont signalées comme amateur ; les autres références non reconnues restent des contenus non classifiés, sans présumer leur statut officiel. `page_basis=cited_page` désigne les pages écrites dans une citation ; `pdf_page` désigne les pages de l'extraction locale. Une ligne de tableur n'est jamais assimilée à une page de livre.

Les annotations portent sur une version précise et sont ignorées après réimport si la version change. `provenance_runs` et `provenance_history` conservent les passes précédentes. `current_document_provenance` expose les annotations valides pour SQL ; `document_source_citations` fournit une ligne par citation, ou une ligne sans titre pour un document sans citation. Les documents originaux, agrégats, décisions et caches Jev restent intacts. L'API de navigation et le futur bundle Strata exposent la même provenance.

Extraction CPU seule (rapport hors dépôt), aperçu par défaut :

```powershell
python tools/sw-kbase/provenance-mine.py --database <knowledge.sqlite> --report <runtime/report.json>
# Ajouter --apply pour persister les annotations.
# --dataset web/wookieepedia-darkstryder permet une actualisation ciblée,
# sans remplacer les annotations des autres collections.
```

`status=identified` signifie qu'un contenu documentaire est explicitement nommé, pas qu'il a été authentifié dans le livre. `citation_unresolved` signifie qu'il ne reste que des renvois vers un site ou des abréviations. `not_stated` indique l'absence de citation exploitable. Aucun statut ne certifie le contenu récupéré.
