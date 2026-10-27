# Jev joint par paragraphes — smoke local du 23 octobre 2026

Clef Flash est utilisable sur la RTX 5090 avec une véritable passe partagée
pour tous les paragraphes fournis. Le test n'a lu ni écrit de données de
campagne. Il ne mesure pas une équivalence de qualité avec Jebadiah 27B.

## Modèle et isolation

- Runtime externe : `<runtime>\jev-joint`.
- Ollaya Windows 0.16.0, serveur loopback `http://127.0.0.1:11438`.
- Tag `clef:flash`, modèle post-entraîné Cloudflare Clef Flash 9B et head joint
  entraîné, révision `17f0b0ad64efb65d273590632833508766b2aae6`.
- Calcul FP32, poids BF16 résidents ; aucune quantification de poids.
- Manifeste public SHA-256
  `32a132a38b50ae33ad68bfbf6ba6b4e192bed9616c407ceadfecdd05ff33d0ea`.
- Quatre shards et head vérifiés intégralement par SHA-256, ainsi que graph,
  tokenizer, template, calibration et binaires/DLLs du runtime. L'identité
  effective complète figure dans `cpu-attestation.json` et les résultats GPU.
- GPU autorisé unique : `GPU-d7f6652e-9fa3-6b5f-024a-e6bc0338dc52`.
  `CUDA_VISIBLE_DEVICES` est fixé avant le démarrage du serveur. L'API atteste
  seulement `cuda:0` ; la sonde SMI ciblée confirme ici la 5090. Avant lancement :
  69 MiB occupés, aucune application compute.
- Téléchargement/assemblage/vérification CPU : 02 h 41 à 02 h 57 environ,
  19,1 Go d'artefacts uniques pour les tags FP32 et FP16 partageant les poids.
  Seul le tag FP32 a été chargé et mesuré.

## Contrat et contrôles

`JointJevClient.from_runtime(runtime_dir)` dans
`packages/sw_kbase/joint_jev.py` expose `model`, `identity`, `capabilities`,
`validate_runtime()`, `preflight(request)` et `__call__(request)`.
`connect=False` autorise le comptage CPU sans serveur ni import CUDA.

Le client ajoute une question native `choice` yes/no/unknown par identifiant
de paragraphe. Tous les paragraphes et toutes les questions sont envoyés dans
un seul `POST /api/decide`. Il n'existe aucune question ni score global.
Les probabilités natives sont conservées ; la sortie applicative les exprime
en pourcentages en corrigeant seulement l'arrondi de l'API.

La limite **globale** de 4096 tokens comprend le state complet, les instructions,
les identifiants et les options de toutes les questions. Le client n'effectue
aucune troncature ni inférence sérielle de secours. Il refuse localement un
dépassement ; la limite exacte 4096 acceptés / 4097 refusés est vérifiée avec
le vrai tokenizer. Les unités d'un document qui dépassent cette limite doivent
être planifiées en lots par le contrôleur ; un document arbitrairement long ne
tient pas dans une seule passe.

Le token count de chaque réponse GPU correspond exactement au préflight local.
Les sorties incomplètes, la troncature serveur, la génération de texte, le mauvais
modèle, la mauvaise précision ou le mauvais device sont des erreurs explicites.
L'identité couvre aussi le prompt de nouveauté et le code de l'adapter ; aucun
cache historique du client Jebadiah n'est réutilisé.

Adapter figé pendant ces mesures : SHA-256
`7e34bae991831ab79da5eb2ef83476ea3ab3b65a877da27dc102998c7a60a237`.

## Mesures réelles

Une référence synthétique de navette XR-7 et huit paragraphes numérotés
contiennent copie exacte, paraphrase, nouveau fait, profil contradictoire 6D
contre 5D, répétition entre candidats, citation seule et autre fait nouveau.
Le cas 4096 tokens ajoute du remplissage synthétique à la référence : c'est
une mesure de coût mémoire/contexte, pas une preuve de qualité documentaire.

| Passe partagée | Répétitions | p50 API | p95 observé | VRAM après appel |
| --- | ---: | ---: | ---: | ---: |
| 8 paragraphes, 1779 tokens, pad 1792 | 7 | 583 ms | 883 ms | 20807 MiB |
| 8 paragraphes, 4096 tokens | 3 | 1384 ms | 1389 ms | 22855 MiB |

Le modèle était chaud avant les mesures. La première forme de lot à 1779
tokens inclut de nouvelles allocations ; en l'excluant, six appels donnent
p50 581 ms et maximum 739 ms. Ces petits échantillons donnent des percentiles
observés, sans estimation robuste de la queue de latence en production.
Le contrôle initial de 162 tokens a chargé le modèle en 15,05 s, avec
145,6 ms d'évaluation et 15,21 s de durée HTTP totale.

121 sondes SMI ont observé un maximum de 22855 MiB, inférieur au guard 28500 MiB.
L'occupation reste constante entre répétitions de même taille. L'échantillonnage
vise 250 ms plus le temps d'exécution du sous-processus SMI ; un pic plus court
peut lui échapper. Le pool conservé après le contexte 4096 reste à 22855 MiB.
Ces chiffres confirment l'absence de conversion intégrale des 9B poids en FP32
sur le GPU pendant ce test.

## Résultats de contrôle et limites de qualité

Les huit argmax correspondent aux attentes synthétiques dans les deux contextes.
Les distributions sont identiques entre répétitions de même entrée. À 1779 tokens :

| Cas | yes | no | unknown |
| --- | ---: | ---: | ---: |
| Copie exacte | 24,41 % | 40,74 % | 34,85 % |
| Paraphrase de la référence | 1,12 % | 96,36 % | 2,52 % |
| Nouveau fait | 88,78 % | 6,32 % | 4,90 % |
| Profil deuxième édition 6D contre première édition 5D | 79,54 % | 8,35 % | 12,11 % |
| Répétition d'un précédent candidat | 1,47 % | 95,96 % | 2,57 % |
| Citation supplémentaire seule | 12,84 % | 48,60 % | 38,56 % |
| Autre nouveau fait | 96,20 % | 1,32 % | 2,48 % |
| Profil 5D déjà couvert | 3,86 % | 92,51 % | 3,63 % |

La copie exacte et la citation seules restent hésitantes. Un argmax correct
ne suffit donc pas à justifier leur suppression automatique ; le contrôleur
doit conserver sa politique prudente et son audit de vrais documents.
Le modèle 9B a un contrat joint approprié et un coût mesuré, sans garantie de
qualité équivalente au 27B ni de calibration spécifique à cette tâche.

## Preuves et état de publication

Dans le runtime externe :

- `cpu-preparation-receipt.json`, `cpu-attestation.json` : artefacts vérifiés.
- `cpu-layout-parity.json` : 18 encodages complets identiques à la référence
  Ollaya, deux contextes refusés et frontière exacte 4096/4097.
- `gpu-smoke-results.json` : requêtes/réponses natives intégrales, probabilités,
  token counts, latences et sondes VRAM ; `gpu-smoke.log` en reprend les résultats.
- `server.err.log` : chargement ONNX et requêtes du serveur.
- `smoke-joint.py` : test synthétique isolé, dix POST groupés mesurés et un
  contrôle de chargement, sans SQLite.
- `warmup-max.json` : copie exacte du wire API testé à 4096 tokens, huit
  questions et `keep_alive=-1`, pour fixer la baseline mémoire du contrôleur
  après allocation du contexte maximal. Son encodage CPU a été revalidé à
  4096 tokens. SHA-256 :
  `cbcb3bf0ef52e830601dd607ebdc8b25c963ee0bb1fd117c81a8137add2fbd41`.

Vérifications offline : 10 tests `sw_joint_jev.py` et 16 tests `sw_native_jev.py`
réussis. L'ancien adapter et son runtime sont inchangés.

Le smoke a publié uniquement `joint-runtime.prepared.json`. Le marqueur final
`joint-runtime.json` relève du contrôleur après revue ; ce smoke ne le publie
pas et ne lance aucune sélection documentaire. Le serveur du smoke a ensuite
été arrêté proprement : PID sorti, port 11438 fermé et 5090 revenue à 58 MiB,
sans application compute, avant remise au contrôleur.
