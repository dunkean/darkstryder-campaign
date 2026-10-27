# Simple-JEV / Qwen 27B standard — benchmark local du 22 octobre 2026

**Correction d'identité : cet essai utilise Simple-JEV et le Qwen standard,
sans adaptation décisionnelle entraînée. Il ne mesure pas les modèles spécialisés
Open-Jev, Jebadiah, AutoTrust JEV ou OneJev.**

**Les deux formats fonctionnent sur la RTX 5090 seule.** À contexte égal, le NF4
est environ 1,5 fois plus rapide en lots sur cet échantillon et conserve une
marge mémoire nettement supérieure. Ce test ne mesure pas la qualité éditoriale.

## Échantillon et conditions

14 documents réels de la base, formant 8 questions : 6 comparaisons d'identité
et 2 typages. Contextes complets de 1 165 à 10 302 caractères ; aucune troncature
silencieuse. Les documents et leurs hashes sont figés dans le même `jobs.json`
pour les deux formats.

Paires : H'ken / H'ken system ; Yapi Spear / Spear ; Mestapol Zephyer /
Mestapol Zephyer light combat fighter ; Datapad / Data Pad ; Jessa Dajus /
Dajus ; AT-ST scout walker / AT-ST. Typages : Tech Armor (Mass Effect) et
Excelsior-Class (Star Trek), présents dans les compilations importées.

Même checkpoint Qwen/Qwen3.8-27B, révision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` ;
Simple-JEV `31c8d7a55a48`, bitsandbytes 0.50.2, Torch 2.14.1+cu130,
Transformers 5.20.2. Int8 seuil 6 ; NF4 double quantification et calcul BF16.
Poids intégralement sur la 5090, sans répartition sur la 4090 ni offload CPU.
Appels HTTP directs au serveur WSL local, sans relais Windows.

## Mesures

| Mesure | Int8 | NF4 / 4 bits |
|---|---:|---:|
| Préparation du serveur, essai complet | 4 min 20 s | 3 min 57 s |
| 8 questions séparées | 16,41 s | 8,46 s |
| Premier passage en lots | 34,89 s | 7,10 s |
| Même passage en lots après échauffement | **10,62 s** | **7,06 s** |
| Débit de ce passage | **0,754 question/s** | **1,133 question/s** |
| Temps amorti par question en lots | 1,33 s | 0,88 s |
| Empreinte des poids | 27,85 Gio | 16,30 Gio |
| Pic mémoire active PyTorch, chargement et inférences | 30,08 Gio | 18,88 Gio |
| Pic d'occupation de la carte, cache et contexte CUDA compris | 31,40 Gio | 19,93 Gio |

Un premier essai int8 a demandé 5 min 13 s de préparation et 21,71 s pour sa
première inférence. Il a été interrompu par un seuil de sécurité trop conservateur,
qui comptait le cache réutilisable comme de la mémoire active. Il est conservé
séparément dans `first-safety-run`. Après correction de cette surveillance,
l'essai int8 complet a réussi sans erreur mémoire. Le premier passage en lots
reste beaucoup plus lent que sa répétition : ne pas utiliser seulement le meilleur
temps pour estimer une première passe sur des formes de requêtes encore inédites.

Le NF4 a aussi été remesuré en séquentiel après les lots : 8,46 s, résultat stable.
Le passage séquentiel final n'a pas été remesuré en int8. Les mesures sont un petit
benchmark exploratoire, sans intervalle statistique ni estimation fiable du corpus entier.

## Limites et observations

- Les lots demandés de quatre questions dépassent ici le plafond de **8 192 tokens**.
  Le client les divise automatiquement : quatre appels d'inférence réussis de deux
  questions, plus deux rejets rapides de budget, tous compris dans les temps.
  Les huit questions ont réellement été inférées ; aucun report de budget n'est
  compté comme une inférence rapide.
- Les 8k viennent de `--max-model-len` dans le lanceur, pas du plafond architectural.
  Le checkpoint annonce `max_position_embeddings=262144`. Une capacité de 16k ou
  32k est configurable, mais **n'a pas été testée ici** et demanderait davantage de
  VRAM. Le NF4 laisse bien plus de marge que l'int8.
- `causal-conv1d` est absent : Transformers signale l'utilisation de son implémentation
  PyTorch de repli. Ces chiffres décrivent l'installation actuelle, sans optimisation
  de ce noyau.
- En lots, les deux formats donnent les mêmes 8 étiquettes dominantes, mais seulement
  4 validations int8 et 2 validations NF4 au seuil existant. En séquentiel : 7 étiquettes
  dominantes identiques sur 8, et 3 validations chacun. Les scores changent aussi avec
  le regroupement. Ces validations sont des franchissements de seuil automatique,
  pas un décompte de bonnes réponses. Sans réponses de référence annotées, cela ne démontre aucune
  équivalence de qualité ni supériorité d'un format.

## Reproduction et état final

Outil : `tools/sw-kbase/benchmark-openjev-documents.py`. Préparer avec
`--prepare-only --database <base> --jobs <runtime/jobs.json>`, puis tester avec
`--jobs <même fichier> --url <serveur local/v1> --revision <révision effective> --output <runtime/résultat.json>`.

Mesures, réponses, métriques serveur, reçus et logs : dossier runtime du classifieur,
`benchmark-2026-10-22/`, notamment `int8-results.json`, `nf4-results.json`,
`int8-status.json`, `nf4-status.json` et les deux rapports `*-runtime.json`.
La base a été ouverte en lecture seule pour l'échantillonnage ; aucune décision
de benchmark n'y a été écrite. Aucun traitement général, Strata ou embedding lancé.
Les deux serveurs de benchmark ont été arrêtés ; la pause de curation est conservée.
