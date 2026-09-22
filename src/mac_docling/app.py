"""Interface locale de conversion PDF et images vers Markdown.

Tout reste sur la machine : aucune statistique n'est envoyée, aucune police
n'est chargée depuis un CDN, le serveur n'écoute que sur la boucle locale et
Docling n'appelle aucun service d'inférence distant.
"""

from __future__ import annotations

import os

# Doit précéder l'import de gradio : la télémétrie est active par défaut et
# poste vers api.gradio.app, contrôle de version compris.
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import logging  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import zipfile  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import gradio as gr  # noqa: E402

from mac_docling.documents import EXTENSIONS_ACCEPTEES, preparer  # noqa: E402
from mac_docling.moteur import ORDRE_NOTES, Moteur, convertir  # noqa: E402

_log = logging.getLogger(__name__)

MOTEUR = Moteur()

COLONNES = ["Page", "Moteur", "parse", "layout", "OCR", "tableaux",
            "total", "confiance", "note", "routée"]

# Police système : gr.themes.GoogleFont ferait charger fonts.googleapis.com
# depuis le navigateur de l'utilisateur.
THEME = gr.themes.Soft(
    font=["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
    font_mono=["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
)

CSS = """
.etat-vide { color: var(--body-text-color-subdued); font-style: italic; }
.bascule { color: #b45309; font-weight: 600; }
footer { display: none !important; }
"""


def _ms(valeur) -> str:
    if valeur in (None, ""):
        return "—"
    return f"{valeur / 1000:.2f} s" if valeur >= 1000 else f"{valeur} ms"


def _ligne_page(donnees: dict) -> list:
    confiance = donnees.get("confiance") or {}
    etapes = donnees.get("etapes") or {}
    moteur = "standard" if donnees["config"] == "A_standard" else "Nanonets"
    if donnees["config"] == "C_nanonets":
        routee = "adoptée" if donnees.get("adoptee") else "écartée"
    else:
        routee = ""
    return [
        donnees["page"], moteur,
        _ms(etapes.get("page_parse")), _ms(etapes.get("layout")),
        _ms(etapes.get("ocr")), _ms(etapes.get("table_structure")),
        _ms(donnees["ms"]),
        f"{confiance['moyenne']:.3f}" if confiance.get("moyenne") is not None else "—",
        confiance.get("note", "—"), routee,
    ]


def traiter(fichiers, seuil, routage_actif) -> Iterator[tuple]:
    """Génère l'état de l'interface au fil des pages converties."""
    if not fichiers:
        yield ("Déposez au moins un fichier.", [], "", None, None)
        return

    dossier = Path(tempfile.mkdtemp(prefix="mac-docling-"))
    travail = dossier / "travail"
    produits: list[Path] = []
    lignes: list[list] = []
    journal: list[str] = []
    apercu = ""

    for index, fichier in enumerate(fichiers, start=1):
        chemin = Path(fichier)
        if chemin.suffix.lower() not in EXTENSIONS_ACCEPTEES:
            journal.append(f"✗ **{chemin.name}** — extension non gérée")
            yield ("\n\n".join(journal), lignes, apercu, None, None)
            continue

        document = preparer(chemin, travail)
        if document is None:
            journal.append(f"✗ **{chemin.name}** — fichier illisible")
            yield ("\n\n".join(journal), lignes, apercu, None, None)
            continue

        entete = f"### {index}/{len(fichiers)} · {document.nom}"
        lignes = []

        for evenement in convertir(document, MOTEUR, seuil, routage_actif):
            donnees = evenement.donnees

            if evenement.genre == "document.debut":
                extra = " · image normalisée" if donnees.get("normalise") else ""
                journal.append(f"{entete}\n{evenement.message}{extra}")

            elif evenement.genre == "modele":
                duree = f" ({_ms(donnees.get('ms'))})" if donnees.get("ms") else ""
                journal.append(f"· {evenement.message}{duree}")

            elif evenement.genre == "page.fin":
                lignes.append(_ligne_page(donnees))
                if donnees.get("alerte"):
                    journal.append(f"⚠ page {donnees['page']} : {donnees['alerte']}")

            elif evenement.genre == "page.bascule":
                journal.append(f"⚡ **{evenement.message}**")

            elif evenement.genre == "erreur":
                journal.append(f"✗ {evenement.message}")

            elif evenement.genre == "document.fin":
                markdown = donnees["markdown"]
                cible = dossier / f"{document.nom}.md"
                cible.write_text(markdown, encoding="utf-8")
                produits.append(cible)
                routees = donnees["pages_routees"]
                journal.append(
                    f"✓ terminé en {_ms(donnees['ms'])} — "
                    f"{donnees['caracteres']} caractères — "
                    + (f"pages routées : {routees}" if routees
                       else "aucune bascule nécessaire")
                )
                apercu = markdown[:4000] + ("\n\n…" if len(markdown) > 4000 else "")

            yield ("\n\n".join(journal), lignes, apercu,
                   [str(p) for p in produits] or None, None)

    archive = None
    if len(produits) > 1:
        archive = dossier / "markdown.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for produit in produits:
                zf.write(produit, produit.name)
        archive = str(archive)

    shutil.rmtree(travail, ignore_errors=True)
    yield ("\n\n".join(journal), lignes, apercu,
           [str(p) for p in produits] or None, archive)


def construire() -> gr.Blocks:
    with gr.Blocks(title="Docling standard + Nanonets",
                   analytics_enabled=False) as interface:
        gr.Markdown(
            "## Conversion locale en Markdown\n"
            "Pipeline Docling standard avec OCR Apple Vision en français. "
            "Chaque page est notée ; celles dont la confiance est faible sont "
            "repassées à Nanonets-OCR2. Rien ne quitte cette machine."
        )

        with gr.Row():
            with gr.Column(scale=2):
                entree = gr.Files(
                    label="PDF et images",
                    file_types=sorted(EXTENSIONS_ACCEPTEES),
                    file_count="multiple",
                )
            with gr.Column(scale=1):
                seuil = gr.Radio(
                    ORDRE_NOTES[:3], value="fair", label="Seuil de bascule",
                    info="Une page notée à ce niveau ou en dessous part au VLM",
                )
                routage = gr.Checkbox(
                    value=True, label="Bascule sur Nanonets-OCR2",
                    info="Décochez pour rester en pipeline standard",
                )
                lancer = gr.Button("Convertir", variant="primary")

        etat = gr.Markdown("Prêt.", elem_classes="etat-vide")
        tableau = gr.Dataframe(
            headers=COLONNES, label="Détail par page", interactive=False,
            wrap=True, column_count=(len(COLONNES), "fixed"),
        )

        with gr.Row():
            fichiers_md = gr.Files(label="Markdown produit", interactive=False)
            archive = gr.File(label="Archive (plusieurs documents)",
                              interactive=False)

        with gr.Accordion("Aperçu du Markdown", open=False):
            apercu = gr.Code(label="", language="markdown", wrap_lines=True)

        lancer.click(
            traiter,
            inputs=[entree, seuil, routage],
            outputs=[etat, tableau, apercu, fichiers_md, archive],
            concurrency_limit=1,   # MPS et MLX se sérialisent de toute façon
        )

    return interface


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("docling").setLevel(logging.ERROR)
    construire().launch(
        theme=THEME,               # police système, jamais gr.themes.GoogleFont
        css=CSS,
        server_name="127.0.0.1",   # jamais 0.0.0.0 : pas d'écoute sur le réseau
        share=False,               # aucun tunnel public
        ssr_mode=False,            # évite le serveur Node supplémentaire
        mcp_server=False,          # n'expose pas l'app comme serveur MCP
        max_file_size="200mb",
        inbrowser=True,
    )


if __name__ == "__main__":
    main()
