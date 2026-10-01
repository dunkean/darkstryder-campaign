# Base de connaissances

Les transcriptions Markdown publiées sous `content/transcriptions/` servent de
preuves pour la base; elles ne remplacent pas le canon éditorial. Chaque assertion
insérée conserve sa citation exacte et la page source.

## Pilote encadré

Le runner est limité aux deux premiers livres originaux:

1. WEG40209, `src-1678b1eb8c4bd691`, DarkStryder Campaign.
2. WEG40118, `src-a3a28719eed85a70`, Kathol Outback.

Il prépare au plus 18 000 caractères et six pages par travail, avec découpage
textuel d'une page plus longue tout en conservant sa vraie page pour les citations.
Le modèle repère les sections logiques dans chaque fenêtre bornée et associe les
nœuds aux sections. Les extraits ne couvrent jamais un livre entier.

Après ces deux livres, la file reste arrêtée à l'étape `awaiting-human-audit`.
Une relecture humaine de la qualité est requise avant d'autoriser Rift, Endgame,
les compléments ou le corpus complet. Le modèle ne valide pas ses propres résultats.
Le périmètre et les travaux prévus sont enregistrés dans
`runtime/kbase/stage.json`; la base SQLite et toutes les réponses/journaux restent
dans le runtime configuré hors Git.

Dans WSL, charger le Node portable du projet:

```sh
source tools/environment.sh
node tools/kbase/runner.mjs init
node tools/kbase/runner.mjs status
```

Lancer au premier plan ou en arrière-plan:

```sh
node tools/kbase/runner.mjs run
node tools/kbase/runner.mjs background
node tools/kbase/runner.mjs stop
```

Un worker est utilisé par défaut; `--workers 2` est la limite supérieure.
`--max-jobs N` borne une session. SIGINT, SIGTERM ou `stop` empêchent la prise
de nouveaux travaux et laissent finir les travaux déjà démarrés. Un verrou évite
de lancer deux files en parallèle. Si le processus est interrompu brutalement,
le prochain lancement marque les travaux restés actifs en échec; il faut ensuite
les relancer explicitement.

Le runner appelle `/usr/bin/codex exec` avec `gpt-6-luna`, une profondeur de
raisonnement basse et la session ChatGPT authentifiée de Codex. Il n'utilise pas
de clé API, n'appelle pas l'API à l'usage et ne remplace jamais le modèle si
l'accès ou la limite d'usage échoue. Les outils shell, navigateur, ordinateur,
applications et MCP sont désactivés; le modèle reçoit uniquement le contrat,
des métadonnées minimales, une liste compacte des nœuds connus et l'extrait
Markdown de son travail.

Chaque erreur arrête la file après une seule tentative. Une erreur de quota est
marquée `blocked-quota`; aucun travail n'est relancé implicitement. Relancer
les travaux échoués est une action explicite:

```sh
node tools/kbase/runner.mjs retry-failed
node tools/kbase/runner.mjs run
```

`status` montre les compteurs de la base et l'étape courante. `audit` affiche
les compteurs et un échantillon des nœuds à relire. `export` écrit
`runtime/kbase/exports/knowledge-v1.json`.

## Contrat des extractions

Les nœuds, relations et sections logiques sont enregistrés avec leurs propres
preuves. Chaque preuve contient `sourceId`, le numéro de page, une citation
exacte et les champs qu'elle justifie. Les propriétés arbitraires sont transportées
dans `propertiesJSON`, puis converties en objet avant l'import transactionnel.
La validation du schéma et des citations garantit la forme et l'ancrage du texte,
pas la vérité éditoriale.

Les preuves sont liées à tous les champs de l'objet concerné; cette liaison est
au niveau de l'objet, pas une validation séparée de chaque champ. Les citations
doivent être relues avec leur contenu. Le runner conserve une copie versionnée
des unités exactes utilisées pour chaque tentative.

Chaque entrée doit contenir un résumé informatif en français des paragraphes
cités, puis un contenu Markdown apportant les faits utiles avec davantage de
détail, organisés par sujet logique. Le contenu paraphrase la source; ce n'est
pas un index de citations ni une répétition du résumé. Il n'y a pas de longueur
minimale artificielle: les faits peu nombreux restent concis et le modèle
n'invente rien pour remplir. Pour un objet déjà connu, seul le fait nouveau de
l'extrait courant est ajouté, sans répéter son contexte connu.

Ces insertions ne remplacent pas une rédaction personnelle. Elles sont
enregistrées comme assertions sourcées à relire, séparément de l'overlay
éditorial modifiable. La fiche affiche le résumé et le contenu Markdown; les
compléments de contenu extraits lors de lectures ultérieures sont affichés dans
une section repliable « Compléments extraits au fil des lectures ». Les preuves
et assertions restent consultables depuis la fiche. L'export `knowledge-v1.json`
inclut pour chaque nœud ses assertions (dont résumé et contenu) avec leurs
citations, ainsi que les relations et sections logiques sourcées.

Les agents écrivent les résumés et contenus en français. Ils préservent les
distinctions entre canon publié, ajouts personnels, interprétation, inspiration et
statistiques générées; traitent les futurs possibles comme possibilités; n'inventent
ni coordonnées, ni liens de commandement, ni événements; et ne créent jamais
d'entrées d'état réel de partie. Les insertions sont marquées GM et restent à relire.

Le modèle sélectionne des unités courtes par identifiant au lieu de recopier ses
citations. Le runner ajoute ensuite le texte exact et le numéro de page issus du
Markdown source; les unités peuvent couvrir plusieurs lignes pour conserver le
contexte des relations.
