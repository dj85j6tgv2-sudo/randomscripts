# Mission : étude d'optimisation du workflow de modèles

Analyse les projets **dispatcher**, **processor** et **alert sender**. Produis une étude technique, exploitable par management, architectes et équipes de développement, pour décider de priorité et de cible d'infrastructure.

## Contexte connu

- Le **dispatcher** lit les configurations de modèles en base de données puis lance leur exécution sur des workers.
- Environ **70 VMs workers**, réparties en **7 clusters de 10 machines**.
- Un ordonnanceur externe (« autocyste », terme à confirmer dans le code/documentation) déclenche les jobs dispatcher dans plusieurs environnements/clusters.
- Comportement actuel : le dispatcher soumet ou exécute des modèles par groupes associés à un cluster. Le groupe constitue une barrière : le traitement suivant attend la fin de tous les modèles du groupe.
- Exemple de blocage : trois modèles sont lancés. Deux finissent rapidement ou n'occupent chacun qu'une machine ; le troisième reste en cours. Les modèles suivants attendent inutilement la fin du troisième.
- Infrastructure actuelle : VMs Red Hat, disques/clusters associés, provisioning et suppression gérés par Terraform.
- Besoin business : absorber ponctuellement des backlogs en ajoutant, par exemple, un cluster temporaire de 10 VMs ; supprimer ce cluster après traitement.
- Option Kubernetes envisagée : cluster Kubernetes de 40–50 VMs. Chaque modèle pourrait déclarer des besoins/limites CPU, mémoire et autres ressources nécessaires (GPU, stockage, contraintes de placement si applicables). Le scheduler placerait les exécutions dès qu'une capacité compatible est libre.
- Point de vigilance : ajout/suppression de VMs dans Kubernetes peut être plus complexe que créer puis détruire un cluster Terraform temporaire. Vérifie ce point selon outils réellement disponibles : Cluster Autoscaler, autoscaling de node groups, Karpenter, autoscaler cloud, politique de scale-down, stockage, contraintes d'entreprise.

## Questions à traiter

1. Comment supprimer l'attente de fin de groupe et lancer les modèles continuellement dès que des ressources sont disponibles ?
2. Comment introduire une planification par ressources, proche de Kubernetes, tout en restant sur VMs/Terraform si pertinent ?
3. Faut-il :
   - optimiser orchestration actuelle et rester sur VMs/Terraform ;
   - conserver VMs/Terraform mais construire un scheduler resource-aware ;
   - migrer directement vers Kubernetes ;
   - adopter une trajectoire hybride/phased migration ?

## Méthode obligatoire

1. Lis code, configuration, CI/CD, manifests, Terraform, documentation, dashboards/observabilité disponibles des trois projets.
2. Cartographie flux complet : déclencheur → dispatcher → lecture DB → création/soumission jobs → allocation worker → processor → alert sender → statut/failure/retry.
3. Distingue explicitement :
   - faits confirmés par code/configuration, avec fichiers et symboles cités ;
   - hypothèses ;
   - informations manquantes et questions à poser.
4. Cherche causes exactes de synchronisation par groupe : `wait`, `join`, polling, batch barrier, capacité figée, locks, dépendances DB, queues, retry, logique de statut ou autre.
5. Évalue impact sur idempotence, retries, timeouts, cancellation, observabilité, sécurité/secrets, données/disques, réseau, coûts, SLA, multi-tenancy et opérations.
6. Ne recommande pas Kubernetes par défaut. Compare coût total, risques et bénéfices contre optimisation ciblée du système actuel.
7. Ne modifie aucun code. Étude seulement, sauf demande explicite contraire.

## Architecture cible à évaluer

Évalue ces mécanismes, seulement si compatibles avec stack existante :

- file de jobs durable ;
- dispatcher qui publie des jobs unitaires ou par unité planifiable, sans barrière globale de batch ;
- workers qui tirent du travail ou scheduler central qui assigne selon capacité ;
- état de ressources par worker : CPU, mémoire, GPU, disque, slots/concurrence, labels/affinités ;
- admission control et réservation atomique pour éviter sur-allocation ;
- priorités, fair-share, quotas par tenant/modèle/environnement ;
- backpressure, limitation de concurrence, deadlines et gestion des jobs bloqués ;
- autoscaling de workers/clusters basé sur backlog, temps d'attente et utilisation, avec scale-down sûr ;
- métriques : profondeur de queue, temps d'attente, throughput, utilisation CPU/mémoire/GPU, fragmentation, taux d'échec/retry, coût/job et coût de capacité idle.

Pour Kubernetes, analyse au minimum : Jobs/CronJobs ou workers long-running, requests/limits, QoS, node selectors/affinity/taints, priority classes, quotas, HPA/KEDA si applicable, Cluster Autoscaler ou équivalent, stockage persistant, logs/métriques, gestion des images et migrations de déploiement.

## Livrable attendu

Écris rapport en français, structuré ainsi :

1. **Résumé exécutif** — décision provisoire, raisons, niveau de confiance.
2. **État actuel confirmé** — diagramme Mermaid du flux et références fichiers/symboles.
3. **Problème de capacité et de barrières** — cause racine, exemple chiffré, impact business/ressources.
4. **Options comparées** — tableau :
   - optimisation minimale sur VMs/Terraform ;
   - scheduler resource-aware sur VMs/Terraform ;
   - Kubernetes ;
   - hybride/migration progressive si pertinente.

   Colonnes : bénéfices, inconvénients, prérequis, complexité, risque, coût, délai, impact sur dispatcher/processor/alert sender, capacité backlog temporaire, réversibilité.
5. **Design recommandé** — fonctionnement, contrats entre projets, modèle de données des ressources/jobs, stratégie de migration sans interruption.
6. **Plan par phases** — quick wins, pilote, critères de sortie, rollback. Chaque phase doit avoir estimation relative et dépendances.
7. **Mesures de décision** — baseline à relever, KPIs, seuils Go/No-Go et expérimentation de charge.
8. **Risques et mitigations** — notamment jobs dupliqués, starvation, overcommit, fuite de ressources, coûts inattendus, scale-down de nœuds avec jobs actifs, volumes, compatibilité Red Hat, dette opérationnelle.
9. **Questions ouvertes** — informations nécessaires avant décision finale.

## Critères de qualité

- Pas d'affirmation non vérifiable présentée comme fait.
- Recommandations reliées aux contraintes observées dans les trois projets.
- Expliquer compromis entre élasticité temporaire Terraform et élasticité Kubernetes.
- Donner actions concrètes, pas uniquement concepts Kubernetes.
- Prioriser optimisation à meilleur ratio valeur/effort avant refonte, sauf preuve contraire.
