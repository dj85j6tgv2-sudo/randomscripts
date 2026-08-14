Regarde les trois projets :
dispatcher
processor
alert sender
Mon sujet, c'est d'étudier les comportements pour optimiser des ressources, optimiser le process totalité de workflow
Dispatcher, il va appeler les configurations des modèles dans la base de données et fait tourner sur des workers. Actuellement on a 70 machines de workers équivalent à sept clusters. 
On a un autocyste qui contrôle les jobs qui tournaient. Donc il va scheduler des jobs de dispatcher dans différents environnements, les clusters, pour pouvoir tourner à différents temps. 
L'actuellement le comportement, c'est qu'on fait un parallèle dispatcher. On regroupe les modèles qui match un cluster de 10 machines et fait tourner. 
Le problème, c'est par exemple si on a trois modèles qui tournent : deux qui ont besoin d'une machine et le troisième modèle qui a besoin d'une autre machine. Il va attendre que les trois sont terminés pour continuer à exécuter les autres. 

Mon sujet, c'est de pouvoir comprendre si on pourrait appliquer des choses comme Kubernetes. Ca veut dire que pour chaque modèle on ajoute des configurations de CPU limite, memory limite et regress comme Kubernetes pour pouvoir tourner sur des machines à volonté à tout moment, dès que ça a assez de ressources, et ne t'aies pas bloqué avec le comportement actuellement. 

J'ai 3 sujets en total :
Comment je peux lancer les modèles en continue et ne pas attendre que le groupe de modèles soit terminé.
Comment facilement implémenter des ressources pour pouvoir tourner sans réfléchir comme un Kubernetes.
Est-ce que ça vaut le coup de switcher directement sur Kubernetes dans ce cas-là ou pas.

Il y a des use case de business. C'est par exemple : des fois ils ont des backlogs donc ils ont besoin de ressources en plus juste pour tourner des modèles en backlog. C'est un besoin spécifique donc avec les architectes actuellement on pourrait d'ajouter un cluster de dix machines, faire tourner et à la fin, quand c'est fini, on peut supprimer.

Ce n'est pas le même cas pour Kubernetes parce qu'on ajoute des machines dans la cluster Kubernetes. Ça va rester là-bas où il faut en faire des étapes en plus pour retirer des workers donc ça veut dire c'est plus complexe dans ce sens-là. C'est des choses qu'on a remarqué. 

 en gros je cherche à pouvoir lister tous les inconvénients et avantages de rester actuellement dans l'infrastructure avec des VM. C'est géré par Terraform donc on commande les machines de Linux en red hat avec Terraform, les clusters de disques, et on fait tourner en fonction du besoin ou on switch sur Kubernetes avec un cluster de 40-50 VM dedans. On change tout le comportement de ces trois projets-là actuellement, le dispatcher qui va appeler les process de Kubernetes pour que deploy soit moderne avec la nouvelle logique orientée vers Kubernetes.

a la fin on reporte à la manager et les équipes pour étudier l'avantage et l'inconvénient et décider qu'est-ce qu'on fait comme priorité. Est-ce qu'on fait juste la partie optimisation, le tourné qu'on a actuellement, on reste avec Terraform, ou on fait Terraform avec un comportement comme Kubernetes ou en Switch directement sur Kubernetes
