# Prochaine phase : référence, votes Jev et fusion structurée

**Remplacée avant exécution sur le corpus par la [v3 groupée](sw-d6-enrichment-v3.md) :
aucun vote global, une réponse multi-scores par document ou lot.** Le reste de ce
document conserve la proposition v2 pour comparaison.

**Historique du 23 octobre, non exécutable comme consigne actuelle.** Le plan
actif utilise désormais les blocs cohérents 2.2 et reste en pause. Voir le
[suivi de cette reprise](sw-d6-reference-workflow-2026-10-23.md).
La rédaction Strata reste désactivée. Le plan et les votes sont deux états
distincts : un document découpé n'est pas encore un document entièrement évalué.

Cette chaîne remplace la première proposition de passe par paragraphes. Elle
reprend **toutes les entrées actuelles**, y compris les singletons et les sources
écartées par le premier tri. Les anciennes décisions restent consultables mais
ne sont pas réutilisées comme verdicts dans cette nouvelle politique.

## 1. Référence explicable et paragraphes numérotés

Par agrégat, choisir une référence selon la source et son origine, la quantité
de texte distinct, les répétitions, la diversité lexicale, la couverture des
termes des autres documents, les données structurées, la provenance disponible
et les défauts de texte. Les poids et priorités sont configurables ; le détail
du classement est conservé. Ce classement ne certifie pas la véracité d'une source.
Il ne change pas l'identifiant de l'entité ni l'ancre historique de l'agrégat.

Découper chaque document selon son format : titres, listes et blocs Markdown,
membres/objets JSON avec leurs chemins, blocs HTML et paragraphes de texte.
Numéros `P00001…`, identifiant stable, offsets exacts, version et empreinte sont
conservés. Les longs paragraphes sont partitionnés en fragments, sans perte de texte.

## 2. Deux niveaux de votes, systématiquement demandés

Pour chaque document supplémentaire, demander à Jev :

1. **Globalement, cette source apporte-t-elle une information utile absente de
   la référence et des paragraphes déjà retenus ?**
2. **Pour chacun de ses paragraphes, apporte-t-il une information absente de
   cette même couverture, enrichie après chaque ajout ?**

Les votes par paragraphe sont demandés **même si le vote global juge la source
inutile**. Ils restent enregistrés, mais ne sélectionnent des ajouts que si
le document est utile ou incertain. Les désaccords global/paragraphe sont signalés.

| Vote global | Vote du paragraphe | Effet |
|---|---|---|
| Redondance **> 80 %** | Quelconque | Vote enregistré, paragraphe non sélectionné |
| Utile ou incertain | Redondance **> 80 %** | Paragraphe redondant |
| Utile ou incertain | Utile ou incertain | Paragraphe ajouté à la couverture |

**80 % exactement conserve le contenu.** Les probabilités oui/non/incertain
restent disponibles ; elles ne constituent pas une mesure de précision validée.
Un paragraphe retenu rejoint immédiatement l'index lexical : le test suivant,
dans le même document ou le suivant, le voit déjà. Une source future ou écartée
ne peut pas couvrir une source antérieure. Le texte des documents est conservé.

Le client natif reste limité à 2 048 tokens par question. Si les deux documents
tiennent, ils sont fournis avec le paragraphe visé. Sinon, le **vote global est
décomposé en blocs couvrant tout le document**, comparés à la couverture au début
du document. Puis les votes par paragraphe utilisent une couverture progressive.
Un index lexical retrouve les passages de référence pertinents ; les fenêtres
et fragments réellement fournis sont enregistrés. Aucun contexte n'est tronqué
silencieusement. Un vote global obtenu par plusieurs blocs est marqué comme
une décision agrégée, avec les scores de ses blocs, pas une probabilité globale
inventée. Un contexte encore impossible à traiter conserve le contenu comme incertain.

Le serveur actuel réalise une inférence par question. La numérotation ne transforme
pas cette interface en réponse générative listant tous les numéros en un seul passage.

## 3. Strata/Qwen local → une entrée riche par entité

Une action séparée transmet au serveur local de rédaction les paragraphes de
référence et les ajouts retenus, par paquets bornés. Le raccord utilise le même
transport local `/v1/chat/completions` que la phase 4 ; modèle, révision, budgets
et adresse sont configurables. Il ne démarre ni Strata ni Qwen automatiquement.

Sortie `sw-rich-entity/1`, une entrée SQLite et une ligne JSONL par entité :
contrat machine dans `config/sw-rich-entity.schema.json`.

- identité stable, nom, type, univers et alias ;
- assertions et champs structurés libres, descriptions, règles et contenu MJ ;
- relations vers les identifiants existants, liens issus du text mining ;
- profils mécaniques et variantes séparés, contradictions et notes ;
- paragraphes sources exacts, source/version/page ou unité, offsets et empreintes ;
- choix de référence, votes des documents et paragraphes, provenance des décisions ;
- overlays personnels conservés séparément, statut `unreviewed`.

Le rédacteur ne peut citer une preuve absente de son paquet, perdre un passage
sélectionné, inventer un identifiant cible ou réunir scénario et référence dans
une seule assertion. Une réduction supplémentaire peut fusionner les assertions
répétées tout en conservant toutes leurs citations. Les profils structurés et
les sources originales restent accessibles indépendamment des choix sur le texte.
Les résultats sont des propositions : la validation des citations n'atteste pas
automatiquement chaque interprétation sémantique du rédacteur.

## Commandes prêtes, non exécutées

CLI : `tools/sw-kbase/enrich.py` ou `npm run sw:enrich -- …`.
Configuration : `config/sw-enrichment.example.json` ; rédacteur désactivé par défaut.

```text
enrich.py --database DB --output NOUVEAU_RUNTIME plan --config CONFIG
enrich.py --database DB --output RUNTIME score --jev-runtime JEV_RUNTIME --execute
enrich.py --database DB --output RUNTIME merge --config CONFIG --execute
enrich.py --database DB --output RUNTIME export --destination EXPORT_RUNTIME
```

`score` et `merge` sont indépendants et respectent `openjev.pause`. Aucun enchaînement
automatique. La base principale reste en lecture seule ; cache et résultats vivent
dans `enrichment.sqlite`, hors dépôt. Les versions, appartenances et overlays sont
contrôlés avant/après traitement et à la reprise. Une modification impose un nouveau
plan ; un export modifié manuellement est protégé. Pas d'embeddings à cette étape.

Tests : `python -m unittest discover -s tests -p sw_enrichment.py`.
