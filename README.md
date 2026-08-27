# Prototype de chatbot LLM-first — Support mobile

## Contenu

- `knowledge_base.json` — les scripts par sujet (mode opératoire, gestion d'erreur, FAQ). **Contient des scripts d'exemple pour 4 sujets (déverrouillage, mise à jour, Wi-Fi) — à remplacer par vos vrais scripts complets.**
- `backend.py` — serveur Python (Flask) qui appelle l'API Claude en injectant les scripts en contexte.
- `static/index.html` — interface de chat web pour tester.
- `requirements.txt` — dépendances Python.

## Installation

```bash
pip install -r requirements.txt
```

Windows PowerShell :
```powershell
$env:GOOGLE_API_KEY="votre_clé_api_gemini"
python backend.py
```

Windows Invite de commandes classique :
```cmd
set GOOGLE_API_KEY=votre_clé_api_gemini
python backend.py
```

Puis ouvrir **http://localhost:5000** dans un navigateur.

## Comment ça fonctionne

1. `knowledge_base.json` contient vos scripts structurés par sujet.
2. À chaque message, `backend.py` envoie à Claude : (a) un prompt système avec des règles strictes ("ne réponds qu'à partir des scripts") + (b) l'intégralité des scripts en contexte + (c) l'historique de la conversation.
3. Claude identifie le sujet, la plateforme (Android/Apple) si besoin, et suit le mode opératoire du script correspondant.
4. Si un sujet n'est pas couvert par les scripts, le modèle est instruit de le dire et de proposer une escalade humaine plutôt que d'inventer une réponse.

## Bibliothèque des sujets non couverts

Quand un utilisateur pose une question qui ne correspond à aucun script de `knowledge_base.json`, le modèle l'indique via un marqueur technique invisible en fin de réponse. Le backend détecte ce marqueur, l'enregistre dans `sujets_non_couverts.json` (créé automatiquement), et retire le marqueur avant d'afficher la réponse à l'utilisateur — il ne voit donc jamais rien d'anormal.

**Pour consulter la liste** (utile pour repérer les sujets les plus demandés et prioriser vos prochains scripts) :
```
http://localhost:5000/admin/sujets-non-couverts
```

Cette page affiche, pour chaque demande non couverte : la date, un résumé du sujet (avec le nombre de fois où ce même sujet est revenu), et le message exact de l'utilisateur.

Une version brute en JSON est aussi disponible sur `http://localhost:5000/api/sujets-non-couverts`, exploitable plus tard pour un export ou un tableau de bord.

⚠️ Le fichier `sujets_non_couverts.json` se construit au fil des conversations. Sur un vrai déploiement, il faudrait le stocker dans une vraie base de données plutôt qu'un simple fichier local (voir section suivante).

## Prochaines étapes possibles

- **Remplacer** `knowledge_base.json` par l'intégralité de vos scripts réels.
- **Ajouter le RAG** si le nombre de scripts devient trop grand pour tenir dans le contexte à chaque appel (recherche du script pertinent avant l'appel au modèle, plutôt que tout envoyer).
- **Ajouter la voix** : brancher un service de speech-to-text (ex. Whisper) en entrée et de text-to-speech (ex. ElevenLabs) en sortie, autour de ce même backend.
- **Escalade humaine réelle** : remplacer la simple mention textuelle par une vraie bascule vers un agent (ex. transfert d'appel, ouverture de ticket).
- **Persistance** : remplacer le dictionnaire `CONVERSATIONS` en mémoire par une vraie base de données pour ne pas perdre l'historique au redémarrage.
