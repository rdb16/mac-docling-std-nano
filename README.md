# mac-docling-std-nano

Conversion locale de PDF et d'images en Markdown, sur Mac Apple Silicon.

Le document est découpé page par page. Chaque page passe au pipeline Docling
standard avec l'OCR Apple Vision en français et reçoit une note de confiance ;
celles qui passent sous le seuil sont repassées à Nanonets-OCR2 via MLX. Les
en-têtes et pieds de page répétés sont retirés avant l'assemblage final.

L'interface affiche une barre d'avancement, le temps de chaque étape, la note
de chaque page et les bascules, au fil de l'eau.

**Rien ne quitte la machine.** Voir la section Confidentialité.

## Installation

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e .
```

Les poids des modèles se téléchargent depuis Hugging Face au premier usage :
environ 0,5 Go pour le pipeline standard et 7,5 Go pour Nanonets-OCR2. Faites
ce premier lancement **sans** `HF_HUB_OFFLINE`, puis réactivez-le.

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
processus serveur pendant toute sa durée. Il signale toute connexion vers
autre chose que `127.0.0.1`.

## Formats acceptés

PDF, et les images `jpg`, `jpeg`, `png`, `tif`, `tiff`, `bmp`, `webp`.

Les formats que Docling ne lit pas (`jp2`, `heic`, `gif`) sont transcodés en
PNG dans un dossier temporaire, et les images dont le plus grand côté dépasse
3000 px sont rééchantillonnées — l'OCR les agrandit ensuite d'un facteur 3, ce
qui dépasserait la protection de Pillow contre les images pièges. Le fichier
d'origine n'est jamais modifié.

## Réglages de l'interface

- **Seuil de bascule** — une page notée à ce niveau ou en dessous part au VLM.
  Docling classe ainsi : `poor` sous 0,5, `fair` sous 0,8, `good` sous 0,9,
  `excellent` au-delà. Défaut : `fair`.
- **Bascule sur Nanonets-OCR2** — décochez pour rester en pipeline standard.
- **Retirer en-têtes et pieds répétés** — voir ci-dessous. Défaut : activé.

Trois boutons : **Convertir**, **Nouvel OCR** qui vide l'écran, et **Fermer le
serveur** qui arrête le processus.

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
