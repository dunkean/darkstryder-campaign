# Phase 4 — fiches agrégées avec sources originales

> Pour la fusion des bases externes, Statbase et transcriptions, utiliser désormais la
> [chaîne SW unifiée](../../docs/sw-d6-unified-pipeline.md) : text mining → OpenJev →
> Qwen + Strata, avec admission sans vérification primaire systématique.
> Entrée compatible : `npm run transcripts:phase4 -- --unified status`.
> Le fonctionnement historique ci-dessous reste disponible sans `--unified`.

La phase 4 est préparée pour les ~60 000 fiches de phase 3. Rien ne démarre automatiquement.
`phase4_merge.py` expose six commandes indépendantes ; **seules `classify` et `aggregate`
appellent un serveur local déjà démarré**. Le client n'installe et ne charge aucun modèle.
La phase 3 et les transcriptions sont lues sans modification. Les anciennes sorties de phase 4
restent conservées ; les nouvelles fiches sont placées dans `corpus-v2/`.

## Fonctionnement

1. `index` : photographie des fiches sous `_extraction/documents/src-*/*/*.md`, de leurs
   identifiants et SHA-256, et des sources publiées. Index SQLite FTS5, noms/alias normalisés,
   passages et vecteurs lexicaux creux (mots et bigrammes hachés), sans GPU ni dépendance Python
   supplémentaire. Les chemins et source IDs sont vérifiés. Un index existant n'est remplacé
   qu'après construction réussie du nouvel index.
2. `candidates` : tous les noms/alias exactement communs, puis au plus 32 voisins supplémentaires
   par fiche via FTS5, trigrammes de noms (variantes/OCR), similarité lexicale et termes contextuels rares. La catégorie n'exclut
   personne. Les paires sont normalisées par ID et dédoublonnées : A–B et B–A sont un seul travail.
   Les correspondances exactes ne sont pas plafonnées. Les autres rapprochements sont des
   candidats, jamais une décision de fusion. On évite une comparaison quadratique de tout le corpus.
3. `classify` : comparaison directe de deux fiches (ID, nom, alias, catégorie, source et contenu),
   via **Simple-JEV + Qwen3.8-27B** sur `/v1/classifier`. Trois réponses : `same_entity`,
   `different_entity`, `uncertain`.
   Les candidats d'une même ancre sont envoyés en lots de huit questions indépendantes,
   avec le contexte de l'ancre partagé ; chaque paire garde son cache et sa propre décision.
   `classifier_batch_size` permet d'ajuster ce lot aux limites du serveur.
   Le client accepte une identité avec un score ≥ 0,92 et une
   marge ≥ 0,20. Ces valeurs sont des réglages initiaux, **pas une garantie d'exactitude** ; les
   probabilités conditionnelles aux choix ne sont pas calibrées pour ce corpus. Commencer par
   examiner un échantillon de paires avant de changer les seuils. Les fiches dépassant
   `classifier_chars` sont explicitement abrégées pour le classement et une réponse positive
   est alors envoyée en révision (`budget_limited`), jamais fusionnée automatiquement.
4. `groups` : une fiche réelle sert d'ancre. Chaque membre doit avoir une décision positive
   directe avec elle ; pas de fermeture transitive A–B–C. Une décision négative ou incertaine
   connue entre membres bloque l'ajout. Les paires non testées entre deux membres ne constituent
   pas une preuve ; l'ancre est le critère commun. Chaque fiche appartient à un seul groupe.
   Les ancres existantes sont réutilisées, les IDs de corpus dérivent de l'ID d'ancre et ne
   changent pas lorsque des sources s'ajoutent. Les conflits restent dans `groups.json`.
5. `aggregate` : le **LLM actuel de phase 3**, sur un endpoint indépendant, réécrit chaque groupe.
   Tous les passages générés sont traités avec des fragments bornés des unités originales
   connues : pages citées, pages profile/role, et page suivante du profil comme en phase 3.
   Les paragraphes résultants sont fusionnés en plusieurs passes si nécessaire. Les appels
   tronqués et les références inventées sont refusés ; une réduction ne peut supprimer des
   preuves déjà citées. Les contradictions et distinctions référence publiée / ajout de campagne /
   source externe doivent rester explicites. Les faits non corroborés par les originaux sont omis.
6. `status` : compteurs locaux en lecture seule, sans appel au serveur.

Une simple mention d'un personnage dans une fiche de planète ne fusionne pas les deux fiches.
Cette version regroupe les fiches **portant sur le même sujet** ; elle n'importe pas automatiquement
des passages de sujets différents. Une espèce et un individu, un modèle et un exemplaire, ou une
organisation et un membre doivent rester distincts, même si leurs noms se ressemblent.

## Configuration

Depuis la racine `darkstryder-campaign-main`, copier puis adapter :

```powershell
Copy-Item config/phase4.example.json config/phase4.local.json
```

`*.local.json` est ignoré par Git. L'exemple reprend l'ID `qwen3.8-flash-next-q2_0` observé dans
les fiches de phase 3 ; remplacer `writer_model` si le serveur présente un autre ID.
`classifier_model` doit exactement correspondre au modèle servi. Les paramètres explicites
en ligne de commande prennent priorité sur le JSON. `classifier_revision` et `writer_revision`
doivent identifier la version réelle des poids/quantification et être changés lorsqu'elle change,
même si l'ID servi reste identique : cela invalide les caches concernés.

`root` désigne **content/transcriptions**, pas la racine du dépôt ; par défaut son chemin est
résolu à partir du script, quel que soit le répertoire courant. `output` vaut `_extraction` par
défaut ; on peut fournir un chemin absolu vers un runtime contenant les fiches de phase 3.

## Préparer le serveur plus tard, lorsque le GPU sera libre

Le client utilise uniquement la bibliothèque standard de Python. Le serveur Simple-JEV doit
avoir son **environnement séparé**, sans toucher à celui de l'OCR. Il n'est pas installé ni
démarré par une commande de pipeline. Exemple d'installation manuelle ultérieure :

```powershell
git clone https://github.com/featherless-ai/simple-jev.git runtime/phase4-classifier/simple-jev
python -m venv runtime/phase4-classifier/.venv
# Installer dans cet environnement le build PyTorch adapté au matériel, puis :
runtime/phase4-classifier/.venv/Scripts/python.exe -m pip install -e ./runtime/phase4-classifier/simple-jev/hf-server
```

Conserver le commit du serveur installé dans les notes du run. Préparer les poids dans le
runtime ou un cache externe. Le lanceur fourni exige un **dossier Hugging Face local existant**
avec `config.json`, empêche les téléchargements et écoute sur 127.0.0.1 :

```powershell
# Action manuelle seulement, une fois le GPU disponible :
./tools/transcriptions/start-classifier.ps1 -ModelPath 'D:/models/Qwen3.8-27B'
```

Le mode BF16 représente environ 54 Go pour les poids seuls, avant caches et buffers ; le
lancement doit être adapté à la mémoire disponible. `device auto` peut répartir sur CPU/GPU,
avec un impact sur le débit. Cette préparation ne prétend pas avoir validé le serveur 27B sur
ce GPU. Le lanceur HF n'accepte pas un GGUF. Pour utiliser le 27B quantifié avec un serveur
OpenAI local déjà disponible, définir `classifier_backend: "chat"`, son URL `/v1` et son ID
servi. Ce repli génère une courte décision JSON ; sa confiance est autodéclarée, et aucune marge
logit n'est disponible. Le backend recommandé reste Simple-JEV.

## Lancement en deux sessions de modèle

Les commandes ci-dessous sont des instructions pour un lancement ultérieur. Aucun `all`/`run`
n'enchaîne implicitement classement et rédaction. Laisser terminer l'extraction avant la
photographie complète ; les nouvelles fiches nécessitent un nouvel `index`, puis `candidates`.

```powershell
# CPU : photographie et présélection
npm run transcripts:phase4 -- index --config config/phase4.local.json
npm run transcripts:phase4 -- candidates --config config/phase4.local.json
npm run transcripts:phase4 -- status --config config/phase4.local.json

# Session du classifieur 27B, lorsqu'il est chargé : essai borné
npm run transcripts:phase4 -- classify --config config/phase4.local.json --limit 100

# Examiner pairs.sqlite et les réponses sous phase4/classification/, puis terminer
npm run transcripts:phase4 -- classify --config config/phase4.local.json
npm run transcripts:phase4 -- groups --config config/phase4.local.json

# Arrêter/décharger le classifieur manuellement, recharger le LLM actuel de phase 3
npm run transcripts:phase4 -- aggregate --config config/phase4.local.json --limit 10
npm run transcripts:phase4 -- aggregate --config config/phase4.local.json
```

`classify --only-id <ID_COMPLET_DE_PHASE3>` borne les paires à celles de cette fiche.
`groups --only-id <ID>` choisit cette fiche en priorité comme ancre, puis couvre tout le corpus.
`aggregate --only-id <ID>` ne rédige que son groupe. Les anciennes ancres déjà persistées ont
priorité après l'ancre explicitement demandée ; changer volontairement une ancre peut changer
l'ID de la fiche agrégée correspondante. `groups` exige que toutes les paires candidates soient
classées ; un essai `--limit` ne permet donc pas de publier des groupes partiels par accident.

Pour améliorer le rappel des variations de noms ou thèmes, augmenter `candidates_per_doc`,
relancer `candidates`, puis classer les nouvelles paires. La présélection lexicale n'assure pas
un rappel parfait pour deux alias sans mots ni contexte communs. La validation réelle des
seuils, du rappel et du débit attend le premier échantillon sur le GPU disponible.

## Sorties, provenance et reprise

Sous `<output>/phase4/` :

- `index.sqlite` : photographie des fiches et du registre des sources ;
- `pairs.sqlite` : scores de présélection, décisions, erreurs et curseur de reprise ;
- `classification/<préfixe>/<empreinte>.json` : réponse brute et décision, deux IDs et empreintes,
  backend/modèle/révision, prompt, seuils et budgets ;
- `groups.json` : ancres, membres, hashes, photographie et conflits d'identité ;
- `review.jsonl` : paires incertaines, noms et scores, produit après `groups` ; les réponses
  détaillées restent disponibles dans `pairs.sqlite` et `classification/` ;
- `writing/` : appels de rédaction validés et cachés ;
- `aggregates/` : état de publication et hashes des sorties ; `errors/` : erreurs par fiche.

Sous `<output>/corpus-v2/<ID_CORPUS>/` :

- `document.md` : fiche avec références originales après chaque paragraphe, plusieurs références
  pour les faits recoupés ; notes avec titre documentaire, PDF/HTML/DOC/etc., page ou segment,
  source ID, chemin original, collection, provenance et hash de l'unité Markdown ;
- `provenance.json` : paragraphes → IDs de preuves → source originale, page/unité, extrait exact,
  offset, hash du Markdown, SHA-256 original, warnings, local corrections, IDs des fiches de phase 3.

Les citations sont générées par le code à partir des IDs de preuves acceptés. Les corrections
locales des transcriptions sont utilisées et signalées, jamais écrasées. La présence exacte de
l'extrait, le numéro d'unité et les hashes sont vérifiables mécaniquement ; le soutien sémantique
de chaque assertion et l'exhaustivité restent à relire (`reviewed: false`).

Chaque étape conserve ses checkpoints. Une interruption de `candidates` reprend au dernier ID
commité ; une interruption de `classify` ne perd que l'appel en cours. Modifier une fiche puis
reconstruire l'index invalide ses paires ; changer modèle/prompt/seuil/révision invalide le classement.
Modifier des originaux ou un groupe invalide la rédaction. Les groupes périmés sont refusés.
Les erreurs restent à reprendre et les étapes sortent en échec lorsqu'un travail demandé échoue.
Une seule étape peut écrire dans un même répertoire à la fois, avec un verrou libéré par le système
même après crash. Aucun lancement en arrière-plan n'est configuré.

La publication refuse de remplacer `document.md` **ou** `provenance.json` modifié localement,
y compris lors d'une reprise. Pour recalculer ailleurs tout en conservant une fiche éditée,
utiliser un autre répertoire d'extraction dédié contenant les entrées de phase 3. Les anciennes
sorties `corpus/`, `decisions/` et `_calls/` du prototype ne sont ni migrées ni supprimées.
Ces nouveaux états runtime et fiches générées sont exclus de Git.

## Validation sans modèle

```powershell
npm run transcripts:phase4:test
npm run check
```

Les tests utilisent exclusivement des dossiers temporaires et des réponses HTTP simulées :
identité directe, alias et catégories, homonymes/conflits, absence de fusion transitive,
seuils, provenance multi-source PDF/HTML, citations invalides, troncature, reprise et protection
des éditions. Ils ne lisent pas le corpus réel et ne contactent aucun serveur d'inférence.

Contrats upstream consultés : [Simple-JEV](https://github.com/featherless-ai/simple-jev),
[API classifier](https://github.com/featherless-ai/simple-jev/blob/main/hf-server/API_REFERENCE.md),
[Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B). Le nom OpenJev recouvre plusieurs
projets ; le client cible précisément ce contrat Simple-JEV, avec un backend chat explicite
pour les serveurs locaux qui exposent seulement `/v1/chat/completions`.
