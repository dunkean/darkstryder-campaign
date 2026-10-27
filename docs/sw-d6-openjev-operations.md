# OpenJev local : exploitation

Le classifieur réutilise le contrat `/v1/classifier` de la phase 4 et les poids
officiels `Qwen/Qwen3.8-27B`. Le rédacteur Qwen/Strata reste une étape distincte.
**État du 22 octobre : OpenJev arrêté à la demande de l'utilisateur, curation en
pause. Seules les interfaces de consultation sont servies.** La reprise et les
essais d'inférence attendent sa prochaine instruction.

Le lanceur sélectionne désormais **la RTX 5090 seule**, par UUID, avant tout import
de CUDA. Il refuse une sélection absente ou ambiguë. Les essais portent sur
l'int8, lots de 2 et contexte 8192 tokens. Les poids ont pu être chargés seuls
(29,91 Go), mais la marge de calcul et l'inférence sur une seule carte n'ont pas
été validées avant l'arrêt. Le NF4 reste une option, jamais appliquée à la curation.
Les mesures int8 ci-dessous décrivent le lancement antérieur sur deux GPU.
Le serveur écoute exclusivement `127.0.0.1:8000` dans WSL. Le client Windows
utilise `http://127.0.0.1:18000/v1` via un relais local, car Windows réserve parfois
le port 8000 et empêche le transfert localhost automatique de WSL. Le relais ne
modifie aucune réservation réseau et ne redémarre pas WSL.

Le code Simple-JEV est épinglé au commit
`31c8d7a55a48e82d21a481799f3044eb45d81c75`, les poids à la révision
`1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`. Les 18 shards représentent
55 563 006 776 octets. Installation et modèles sont hors Git, dans un venv
WSL dédié et un répertoire runtime sur le disque disposant d'assez d'espace.

Dans WSL, définir `OPENJEV_VENV`, `SW_KBASE_CHECKOUT` et `SW_KBASE_RUNTIME`
avec les chemins Linux locaux. Le reçu externe `openjev-wsl-install.json`
conserve leurs valeurs et les commandes exactes Linux et PowerShell utilisées
sur cette machine (`launch_command_wsl` et `launch_command_powershell`). Depuis
PowerShell, `wsl -d Ubuntu-22.04` ouvre la distribution pour la commande suivante.

```sh
nohup "$OPENJEV_VENV/bin/python" \
  "$SW_KBASE_CHECKOUT/tools/sw-kbase/start-openjev-wsl.py" \
  --model "$SW_KBASE_RUNTIME/models/Qwen3.8-27B" \
  --revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0 \
  --gpu-name 5090 --quantization int8 --weight-io pread \
  --max-batch-size 2 --max-model-len 8192 --gpu-reserve-gib 2 \
  --report "$SW_KBASE_RUNTIME/openjev-wsl-5090-report.json" \
  > "$SW_KBASE_RUNTIME/openjev-wsl-5090.log" 2>&1 &
OPENJEV_PID=$!
```

Après un arrêt contrôlé, un nouveau démarrage doit renouveler le reçu de
processus. Dans le même shell WSL, enregistrer immédiatement le nouveau PID :

```sh
"$OPENJEV_VENV/bin/python" - "$SW_KBASE_RUNTIME" "$OPENJEV_PID" <<'PY'
import json, sys
from pathlib import Path
base, pid = Path(sys.argv[1]), int(sys.argv[2])
proc = Path('/proc') / str(pid)
path = base / 'openjev-wsl-install.json'
receipt = json.loads(path.read_text()) if path.exists() else {}
receipt.update(pid=pid, status='loading',
    launch_argv=[s.decode() for s in (proc/'cmdline').read_bytes().split(b'\0') if s],
    server_start_ticks=(proc/'stat').read_text().split(') ', 1)[1].split()[19])
path.write_text(json.dumps(receipt, indent=2) + '\n')
PY
```

L'option int8 applique `BitsAndBytesConfig(load_in_8bit=True)` aux mêmes shards
BF16, sans nouveau téléchargement. Elle refuse un placement CPU ou disque.
Le lanceur réserve de la mémoire pour l'inférence et prend le minimum des
mesures CUDA et `nvidia-smi`, car WSL peut sous-estimer l'usage graphique Windows.
La lecture `pread` évite les petits accès mmap à travers `/mnt` ; elle ne change
pas les poids. Le chargement est hors ligne. L'environnement OCR reste séparé.
`fla-core` / `flash-linear-attention` 0.5.2 fournissent les noyaux Triton de
GatedDeltaNet utilisés par cette architecture. Leurs calculs BF16 ont été testés
sur les deux GPU ; leur version et celle de Triton participent à la révision.
Le lanceur corrige aussi les indices `logits_to_keep` du calcul multi-GPU : ces
petits indices passent sur CPU à l'intérieur du hook Accelerate pour rester
valides quand les états finaux sont sur le second GPU. Le marqueur
`cpu-logit-indices-v1` et le seuil int8 explicite 6 figurent dans la révision.

Le rapport contient `effective_revision`, la carte GPU effective, les versions,
la quantification et la politique de prompt `examples_binary`. Copier cette
révision complète dans `semantic_revision` du client : une modification de
modèle, quantification ou politique exige une nouvelle révision de cache.
Ne pas utiliser simplement `local` pour une curation persistée.

Le reçu externe contient aussi PID, commande, journaux et résultats des tests.
Après le démarrage WSL, lancer le relais depuis le checkout Windows :

```powershell
python tools/sw-kbase/openjev-wsl-relay.py --distribution Ubuntu-22.04 `
  --port 18000 --upstream-port 8000 `
  --runtime-report "$Runtime/openjev-wsl-5090-report.json" `
  --runtime-receipt "$Runtime/openjev-wsl-install.json"
```

Il écoute uniquement `127.0.0.1`, expose seulement GET `/v1/models` et POST
`/v1/classifier`, et transmet ces requêtes via un worker Python stdlib WSL vers
le loopback WSL. Le worker vérifie le PID, son instant de création et sa commande
exacte avant et après chaque appel. Le relais refuse un rapport ou un reçu
modifié durant l'appel. `local_runtime.effective_revision` est explicitement
marqué `revision_source=local_launcher_report`. Aucune CORS permissive,
redirection ou route de fichier n'est ajoutée.
Vérifier `/v1/models`, puis exécuter depuis le checkout Windows :

```powershell
python tools/sw-kbase/benchmark-openjev.py `
  --url http://127.0.0.1:18000/v1 `
  --revision $EffectiveRevision --context-chars 10000 --rounds 2 `
  --output "$Runtime/benchmark-int8-long.json"
```

Le benchmark utilise huit cas explicitement synthétiques avec `TARGET` et
`CANDIDATE`, sans écrire dans une base. Ses résultats gardent durée, réponses
brutes, probabilités et découverte serveur. Les essais sur données réelles
passent ensuite par une copie pilote dont les résultats ne sont importés qu'après
validation des empreintes et versions des records.

Le client `sw-semantic-5` place les preuves une seule fois dans `state`, avec un
contexte distinct nommé pour chaque question. Le renderer `examples_binary`
répète les instructions : y mettre les documents multipliait leur longueur.
Un rejet de longueur avant inférence divise le lot ; un document seul qui ne
tient toujours pas reste explicitement en attente de contexte, sans verdict inventé.

Mesures obtenues avec les paramètres ci-dessus : huit identités en 43,80 s au
premier appel corrigé, puis 2,82 s à chaud ; avec 10 000 caractères partagés,
17,57 s puis 3,64 s. Les 32 décisions d'identité étaient correctes et acceptées.
Un lot de huit classifications de type, avec 21 choix chacune, a donné huit
bonnes réponses en 12,43 s. Ces fixtures valident le transport et le calcul ; la
qualité sur le corpus réel est évaluée séparément. Aucun débit BF16 complet n'a
été mesuré : les essais de chargement HDD ont été interrompus. Le premier appel
int8 échoué après 337,50 s reste archivé comme échec multi-GPU, pas comme débit.

Après le mining et le pilote, `python tools/sw-kbase/curate-all.py --config
config/sw-kbase.local.json` enchaîne les identités puis les classes DarkStryder,
les autres identités, les relations de campagne et le reste de la file.
Il utilise uniquement `curate`, jamais `compose`. Les étapes sont visibles dans
`curation-plan.json` ; les compteurs, heures et abstentions sans inférence sont
dans `openjev-progress.json`, à côté de la base. Une pause arrête aussi ce plan.
Après `resume`, relancer la même commande : les tâches déjà traitées sont réutilisées.
Le plan retente les erreurs enregistrées ; une nouvelle erreur arrête le plan
et reste visible, sans déclarer la curation terminée.

Pour arrêter le serveur, vérifier que le PID enregistré désigne ce lanceur avant
d'envoyer SIGTERM. Ne pas arrêter un processus par nom générique Python ou CUDA.

Une adaptation explicite de capacité peut conserver les verdicts antérieurs :
`continue-contract --previous-config <ancienne-config> --reason <motif>`. Elle
vérifie l'empreinte précédente, autorise uniquement révision et taille de lots,
enregistre les deux révisions dans `semantic_contract_history` et ne retouche
aucun verdict ni regroupement existant. Les seuils, le modèle et les tâches
restent identiques. Sans cette opération, un contrat modifié reste refusé.

Console SQL locale : `npm run sw:kb:sql`, puis **http://127.0.0.1:8766/query/**.
Elle utilise sqlite-web 0.8.2 dans son venv runtime dédié et ouvre la base active
en lecture seule. Pour retrouver une fiche :

```sql
SELECT r.id, v.name, v.kind, r.entity_id
FROM records r JOIN record_versions v ON v.id = r.current_version
WHERE r.active = 1 AND v.name LIKE '%FarStar%'
LIMIT 50;
```

État au 22 octobre 2026 : import et mining terminés, curation OpenJev en cours
en arrière-plan, priorité DarkStryder. La base et les poids sont sur le SSD C: ;
`config/sw-kbase.local.json` indique le chemin actif. La base contient 74 541
records actifs, 113 695 passages et 87 583 liens documentaires. L'audit a vérifié
les 74 541 hashes courants et l'intégrité SQLite sans erreur. Les 12 fichiers
de l'export `2026-10-22-reference-ready` ont leurs hashes vérifiés ; cet export
est un instantané de curation partielle. Le reçu `run-report.json`, à côté de
la base, rassemble chemins, compteurs et vérifications. Strata et les embeddings
n'ont pas été exécutés.
