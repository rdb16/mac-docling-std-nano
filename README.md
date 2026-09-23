# mac-docling-std-nano

**Un OCR pour documents sensibles.** Conversion de PDF et d'images en
Markdown sur Mac Apple Silicon, entièrement hors ligne : aucune donnée, aucun
extrait de texte, aucune métadonnée ne sort de la machine. Pas de service
d'OCR en ligne, pas d'API, pas de téléversement — donc rien à faire valider
par un DPO, et des pièces comptables, dossiers médicaux, contrats ou fiches de
paie qui restent là où ils sont.

Le moteur est **[Docling](https://github.com/docling-project/docling) 2.129**,
la bibliothèque d'analyse documentaire d'IBM Research, utilisée ici dans ses
deux pipelines et sans aucun de ses connecteurs distants.

Le document est découpé page par page. Chaque page passe au pipeline Docling
standard avec l'OCR Apple Vision en français et reçoit une note de confiance ;
celles qui passent sous le seuil sont repassées à Nanonets-OCR2 via MLX. Les
en-têtes et pieds de page répétés sont retirés avant l'assemblage final.

L'interface affiche une barre d'avancement, le temps de chaque étape, la note
de chaque page et les bascules, au fil de l'eau.

## Ce qui fait tourner tout ça

| Composant | Version | Rôle |
|-----------|---------|------|
| [`docling`](https://github.com/docling-project/docling) | 2.129.0 | orchestration des deux pipelines, notes de confiance, export Markdown |
| `docling-core` | 2.98.0 | modèle de document et sérialisation |
| `docling-ibm-models` | 4.0.3 | modèles de mise en page et TableFormer |
| `docling-parse` | 7.21.0 | lecture bas niveau des PDF |
| `ocrmac` | 1.0.1 | pont vers Apple Vision, l'OCR fourni avec macOS |
| `mlx` / `mlx-vlm` | 0.32.2 / 0.7.2 | exécution de Nanonets-OCR2 sur le GPU Apple |
| `gradio` | 6.28.0 | interface web, servie sur la boucle locale seulement |

Le `pyproject.toml` épingle `docling[ocrmac,vlm]>=2.129,<3` : la borne haute
évite qu'une version majeure change les noms de pipelines ou le calcul des
notes de confiance sans prévenir. Le `uv.lock` versionné va plus loin et fige
les 165 paquets de la résolution, aux versions exactes de ce tableau — celles
sur lesquelles les mesures de ce README ont été faites.

Aucune de ces briques n'appelle de service distant dans cette configuration —
c'est vérifiable, voir Confidentialité.

## Installation

```bash
uv sync --frozen
```

`--frozen` interdit à uv de recalculer la résolution : vous obtenez le contenu
exact de `uv.lock`, et non la dernière version compatible du moment. Sans ce
drapeau, uv réécrirait le verrou et pourrait installer un Docling plus récent
que celui sur lequel l'outil a été mesuré.

Pour modifier le code plutôt que seulement l'utiliser, l'installation éditable
reste possible — mais elle ignore le verrou :

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e .
```

Les tests et le linter sont dans le groupe `dev`, installé par défaut par
`uv sync` :

```bash
uv run pytest
uv run ruff check .
```

## Préchargement des modèles

L'application positionne `HF_HUB_OFFLINE=1` par défaut, qu'on la lance par
`lancer.sh` ou directement. Au lancement, plus rien n'est donc téléchargé :
les poids doivent être présents **avant**. Sans le pipeline standard, les
documents échouent un à un ; sans Nanonets-OCR2, les pages faibles restent
en pipeline standard et le journal le signale. Comptez environ 8 Go.

Les trois dépôts sont publics, aucune authentification Hugging Face n'est
nécessaire :

```bash
# Pipeline standard — analyse de mise en page
.venv/bin/hf download docling-project/docling-layout-heron --revision main

# Pipeline standard — structure des tableaux.
# La révision est figée : Docling demande v2.3.0, pas main.
.venv/bin/hf download docling-project/docling-models --revision v2.3.0

# Bascule VLM — Nanonets-OCR2 quantifié pour MLX
.venv/bin/hf download mlx-community/Nanonets-OCR2-3B-bf16 --revision main
```

| Dépôt | Rôle | Sur disque |
|-------|------|------------|
| `docling-project/docling-layout-heron` | découpage de la page en régions | 172 Mo |
| `docling-project/docling-models` (`v2.3.0`) | structure des tableaux (TableFormer) | 358 Mo |
| `mlx-community/Nanonets-OCR2-3B-bf16` | bascule des pages faibles | 7,5 Go |

L'OCR n'apparaît pas dans cette liste : Apple Vision est fourni avec macOS.

Si vous ne comptez pas activer la bascule, les deux premiers dépôts suffisent
— décochez alors **Bascule sur Nanonets-OCR2** dans l'interface. Les 7,5 Go de
Nanonets ne se justifient que pour les scans et les tickets de caisse, où le
pipeline standard perd le texte classé en image.

### Vérifier que le cache suffit

Tout arrive dans `~/.cache/huggingface/hub`, que Docling relit ensuite hors
ligne. Cette commande construit le pipeline standard sans réseau :

```bash
HF_HUB_OFFLINE=1 .venv/bin/python -c \
  "from mac_docling.moteur import Moteur; Moteur().standard()"
```

Quelques secondes puis aucune erreur : le cache est complet.

N'utilisez pas `docling-tools models download` pour ce préchargement : cette
commande écrit dans `~/.cache/docling/models`, que l'application ne lit pas.
`moteur.py` ne renseigne pas `artifacts_path`, Docling résout donc par le
cache Hugging Face.

À défaut de préchargement, un premier lancement en ligne fait le travail, au
prix de l'attente et sans barre de progression :

```bash
HF_HUB_OFFLINE=0 ./lancer.sh
```

## Lancer

```bash
./lancer.sh
```

L'interface s'ouvre sur <http://127.0.0.1:7860>.

## Pourquoi page par page

Docling ne donne la confiance qu'une fois la conversion faite, et la note du
document masque ses pages faibles : un rapport noté *good* à 0,83 contenait
une page à 0,67 que seul un examen par page repère.

Convertir page par page coûte 1,1× le temps d'une conversion globale, pour des
notes identiques. Ce surcoût achète deux choses : l'affichage en direct dès
qu'une page est prête, et l'envoi au VLM des seules pages faibles. Sur un
rapport de quatre pages dont une est faible, cela représente une page à
Nanonets au lieu de quatre — soit environ 15 s au lieu de 60 s.

## Confidentialité

C'est la raison d'être de ce dépôt. Un OCR en ligne suppose de téléverser le
document : pour une facture, un bilan sanguin ou un contrat, cela signifie
confier la pièce à un tiers, avec la rétention et la localisation qu'il
pratique. Ici le document ne quitte pas le disque, et Docling est configuré
pour que ses propres échappatoires réseau soient fermées.

Cinq verrous, posés dans `lancer.sh` et `app.py` :

| Verrou | Ce qu'il bloque |
|--------|-----------------|
| `GRADIO_ANALYTICS_ENABLED=False` et `analytics_enabled=False` | Gradio poste sinon vers `api.gradio.app`, contrôle de version compris |
| Police système, jamais `gr.themes.GoogleFont` | le chargement de `fonts.googleapis.com` par le navigateur |
| `server_name="127.0.0.1"`, `share=False` | toute écoute hors boucle locale, tout tunnel public |
| `HF_HUB_OFFLINE=1` | les appels à `huggingface.co/api/models` pour vérifier les révisions |
| `enable_remote_services=False` | tout service d'inférence distant côté Docling |

Ces affirmations se vérifient :

```bash
./lancer.sh &
.venv/bin/python verifier_reseau.py mon_document.pdf
```

Le script lance une conversion réelle et échantillonne les connexions du
processus serveur toutes les 0,4 s pendant toute sa durée, via `lsof`. Il
signale toute connexion vers autre chose que la boucle locale.

### Portée exacte de la garantie

Ce qui est couvert :

- **Aucune sortie réseau pendant la conversion**, mesurée et non supposée.
- **Aucun service d'inférence distant** : `enable_remote_services=False`
  côté Docling, et les deux modèles tournent sur le GPU de la machine.
- **Aucune trace laissée après la fermeture du serveur** : les copies des
  documents déposés (cache de Gradio), les images normalisées, les Markdown
  produits et l'archive vivent sous un unique dossier temporaire, supprimé
  par le bouton **Fermer le serveur**, par Ctrl-C, par `kill` ou à la
  fermeture du terminal.

Ce qui ne l'est pas, et qu'il faut savoir :

- Le **téléchargement initial des modèles** passe par Hugging Face. Il ne
  transmet aucun document, mais c'est le seul moment où la machine parle au
  réseau. Faites-le avant, une fois pour toutes.
- Les **Markdown produits disparaissent avec le serveur** : téléchargez-les
  avant de le fermer. Un arrêt brutal (`kill -9`, coupure de courant) ne
  laisse pas le temps de nettoyer ; le dossier `mac-docling-*` reste alors
  dans le dossier temporaire du système.
- Le chiffrement du disque et l'accès physique au poste relèvent de macOS,
  pas de cet outil.

## Formats acceptés

PDF, et les images `jpg`, `jpeg`, `png`, `tif`, `tiff`, `bmp`, `webp`.

Les formats que Docling ne lit pas (`jp2`, `gif`) sont transcodés en
PNG dans un dossier temporaire, et les images dont le plus grand côté dépasse
3000 px sont rééchantillonnées — l'OCR les agrandit ensuite d'un facteur 3, ce
qui dépasserait la protection de Pillow contre les images pièges. Le fichier
d'origine n'est jamais modifié.

Les photos `heic` et `heif` sont refusées : trop volumineuses pour l'OCR.
Exportez-les d'abord en JPEG.

## Réglages de l'interface

- **Seuil de bascule** — une page notée à ce niveau ou en dessous part au VLM.
  Docling classe ainsi : `poor` sous 0,5, `fair` sous 0,8, `good` sous 0,9,
  `excellent` au-delà. Défaut : `fair`.
- **Bascule sur Nanonets-OCR2** — décochez pour rester en pipeline standard.
- **Retirer en-têtes et pieds répétés** — voir ci-dessous. Défaut : activé.

Quatre boutons : **Convertir** ; **Annuler**, qui interrompt le lot entre deux
pages ; **Nouvel OCR**, qui l'interrompt aussi et vide l'écran ; **Fermer le
serveur**, qui arrête le processus et supprime les fichiers de la session.

Quand le lot compte plusieurs documents, la liste **Document affiché** permet
de revenir au détail et au Markdown de chacun.

## Déduplication des en-têtes et pieds de page

Les pages étant converties séparément, chacune rapporte l'en-tête et le pied
du document. Concaténées telles quelles, ces lignes reviennent autant de fois
qu'il y a de pages.

Le critère retenu est la fréquence **entre les pages**, pas la position dans
la page : après remise en ordre de lecture, Docling place souvent l'en-tête au
milieu du Markdown. Une ligne vue sur au moins 60 % des pages d'un document
d'au moins trois pages est tenue pour un en-tête ou un pied ; sa première
occurrence est conservée, les suivantes sont retirées. Les lignes de tableau
sont épargnées : deux pages peuvent légitimement porter la même donnée.

Mesuré sur un compte rendu de laboratoire de treize pages : 276 lignes
retirées sur 691, soit 18 % de caractères en moins, sans perte de contenu.
Sur une facture d'une page, rien n'est retiré.

## Limites connues

- **Un tableau à cheval sur deux pages est coupé en deux**, puisque les pages
  sont converties séparément puis concaténées.
- **Le texte des régions que Docling classe comme `picture` est perdu** en
  pipeline standard. C'est ce qui rend la bascule indispensable sur les
  tickets de caisse scannés : l'OCR lit le texte, mais la mise en page le
  classe en image et l'export n'en garde rien.
- **La déduplication est irréversible dans le fichier produit.** Décochez-la
  si vos documents répètent légitimement les mêmes phrases d'une page à
  l'autre. Le nombre de lignes retirées est toujours affiché.
- **La note de confiance n'est pas une mesure de fidélité.** Elle juge la
  cohérence interne de l'analyse. Une page peut être notée *good* et rendre un
  texte pauvre.
- **Nanonets occupe environ 6 Go de mémoire unifiée** une fois chargé. Il ne
  l'est qu'au premier basculement.
- À l'arrêt du serveur, Docling peut afficher une `AttributeError` dans
  `VlmConvertModel.__del__` : le module de journalisation est déjà démonté.
  Sans conséquence.

## Licence

Ce dépôt est publié sous licence **MIT** : voir [LICENSE](LICENSE). Vous pouvez
l'utiliser, le modifier et le redistribuer, y compris commercialement, à
condition de conserver la notice de copyright.

Les dépendances gardent leur propre licence. Toute la chaîne Docling est sous
MIT, comme `ocrmac`, `mlx` et `mlx-vlm` ; `gradio` est sous Apache-2.0 :

| Composant | Licence |
|-----------|---------|
| `docling`, `docling-core`, `docling-ibm-models`, `docling-parse` | MIT |
| `ocrmac`, `mlx`, `mlx-vlm` | MIT |
| `gradio` | Apache-2.0 |

Les **poids des modèles ne sont pas couverts par cette licence** et ne sont
pas redistribués ici : le dépôt ne fait qu'indiquer comment les télécharger.
Vérifiez leurs conditions propres avant un usage commercial, en particulier
celles de [Nanonets-OCR2-3B](https://huggingface.co/nanonets/Nanonets-OCR2-3B),
dérivé de Qwen2.5-VL-3B-Instruct.
