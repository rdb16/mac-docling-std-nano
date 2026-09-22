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

import base64  # noqa: E402
import html  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import zipfile  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import gradio as gr  # noqa: E402

from mac_docling.documents import EXTENSIONS_ACCEPTEES, preparer  # noqa: E402
from mac_docling.moteur import ORDRE_NOTES, Moteur, convertir  # noqa: E402

_log = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
MOTEUR = Moteur()

COLONNES = ["Page", "Moteur", "parse", "layout", "OCR", "tableaux",
            "total", "confiance", "note", "bascule"]

# Police système : gr.themes.GoogleFont ferait charger fonts.googleapis.com
# depuis le navigateur.
THEME = gr.themes.Soft(
    font=["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
    font_mono=["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
)

CSS = """
footer { display: none !important; }
#bandeau { display: flex; align-items: center; gap: 14px; margin-bottom: 4px; }
#bandeau img { width: 44px; height: 44px; border-radius: 9px; }
#bandeau h1 { margin: 0; font-size: 20px; line-height: 1.2; }
#bandeau p { margin: 2px 0 0; font-size: 13px; opacity: .72; }
.piste {
  height: 8px; border-radius: 99px; overflow: hidden;
  background: var(--neutral-200); margin: 10px 0 4px;
}
.jauge { height: 100%; border-radius: 99px; background: var(--primary-500);
         transition: width .25s ease; }
.jauge.finie { background: #15803d; }
.legende { font-size: 12px; opacity: .75; display: flex;
           justify-content: space-between; }
.arret { padding: 14px; border-radius: 10px; text-align: center;
         background: #fef2f2; color: #991b1b; font-weight: 600; }
"""


def _donnee_uri(chemin: Path, type_mime: str = "image/png") -> str:
    return (f"data:{type_mime};base64,"
            f"{base64.b64encode(chemin.read_bytes()).decode('ascii')}")


def _bandeau() -> str:
    logo = ASSETS / "logo.png"
    image = (f'<img src="{_donnee_uri(logo)}" alt="SNTPK">' if logo.exists() else "")
    return (
        f'<div id="bandeau">{image}<div>'
        "<h1>Conversion locale en Markdown</h1>"
        "<p>Docling standard avec OCR Apple Vision en français · bascule sur "
        "Nanonets-OCR2 page par page · rien ne quitte cette machine</p>"
        "</div></div>"
    )


def barre(faites: int, total: int, note: str = "") -> str:
    """Barre d'avancement en haut de page."""
    part = (faites / total * 100) if total else 0
    fini = " finie" if total and faites >= total else ""
    return (
        f'<div class="piste"><div class="jauge{fini}" style="width:{part:.1f}%">'
        f'</div></div><div class="legende"><span>{html.escape(note)}</span>'
        f'<span>{faites}/{total} page(s)</span></div>'
    )


def _ms(valeur) -> str:
    if valeur in (None, ""):
        return "—"
    return f"{valeur / 1000:.2f} s" if valeur >= 1000 else f"{valeur} ms"


def _ligne_page(donnees: dict) -> list:
    confiance = donnees.get("confiance") or {}
    etapes = donnees.get("etapes") or {}
    moteur = "standard" if donnees["config"] == "A_standard" else "Nanonets"
    if donnees["config"] == "C_nanonets":
        bascule = "adoptée" if donnees.get("adoptee") else "écartée"
    else:
        bascule = ""
    return [
        donnees["page"], moteur,
        _ms(etapes.get("page_parse")), _ms(etapes.get("layout")),
        _ms(etapes.get("ocr")), _ms(etapes.get("table_structure")),
        _ms(donnees["ms"]),
        f"{confiance['moyenne']:.3f}" if confiance.get("moyenne") is not None else "—",
        confiance.get("note", "—"), bascule,
    ]


def traiter(fichiers, seuil, routage_actif, dedupliquer) -> Iterator[tuple]:
    """Génère l'état de l'interface au fil des pages converties."""
    vide = (barre(0, 0, "en attente"), "Déposez au moins un fichier.",
            [], "", "", None, None)
    if not fichiers:
        yield vide
        return

    dossier = Path(tempfile.mkdtemp(prefix="mac-docling-"))
    travail = dossier / "travail"

    # On prépare tout d'abord pour connaître le nombre total de pages : la
    # barre d'avancement doit porter sur le lot entier, pas sur un document.
    documents = []
    refus = []
    for fichier in fichiers:
        chemin = Path(fichier)
        if chemin.suffix.lower() not in EXTENSIONS_ACCEPTEES:
            refus.append(f"✗ **{chemin.name}** — extension non gérée")
            continue
        document = preparer(chemin, travail)
        if document is None:
            refus.append(f"✗ **{chemin.name}** — fichier illisible")
            continue
        documents.append(document)

    total_pages = sum(max(d.pages, 1) for d in documents)
    if not documents:
        yield (barre(0, 0, "aucun fichier exploitable"), "\n\n".join(refus),
               [], "", "", None, None)
        return

    produits: list[Path] = []
    lignes: list[list] = []
    journal: list[str] = list(refus)
    markdown_final = ""
    faites = 0

    def etat(note: str):
        return (barre(faites, total_pages, note), "\n\n".join(journal), lignes,
                markdown_final, markdown_final,
                [str(p) for p in produits] or None, None)

    for index, document in enumerate(documents, start=1):
        entete = f"### {index}/{len(documents)} · {document.nom}"
        lignes = []
        vues_standard: set[int] = set()

        for evenement in convertir(document, MOTEUR, seuil, routage_actif,
                                   dedupliquer):
            donnees = evenement.donnees

            if evenement.genre == "document.debut":
                extra = " · image normalisée" if donnees.get("normalise") else ""
                journal.append(f"{entete}\n{evenement.message}{extra}")

            elif evenement.genre == "modele":
                duree = f" ({_ms(donnees.get('ms'))})" if donnees.get("ms") else ""
                journal.append(f"· {evenement.message}{duree}")

            elif evenement.genre == "page.fin":
                lignes = lignes + [_ligne_page(donnees)]
                # L'avancement compte les pages, pas les passages : une page
                # repassée au VLM ne la fait pas avancer deux fois.
                if donnees["config"] == "A_standard":
                    numero = donnees["page"]
                    if numero not in vues_standard:
                        vues_standard.add(numero)
                        faites += 1
                if donnees.get("alerte"):
                    journal.append(f"⚠ page {donnees['page']} : {donnees['alerte']}")

            elif evenement.genre == "page.bascule":
                journal.append(f"⚡ **{evenement.message}**")

            elif evenement.genre == "erreur":
                journal.append(f"✗ {evenement.message}")

            elif evenement.genre == "document.fin":
                markdown_final = donnees["markdown"]
                cible = dossier / f"{document.nom}.md"
                cible.write_text(markdown_final, encoding="utf-8")
                produits.append(cible)
                routees = donnees["pages_routees"]
                journal.append(
                    f"✓ terminé en {_ms(donnees['ms'])} — "
                    f"{donnees['caracteres']} caractères — "
                    + (f"pages routées : {routees}" if routees
                       else "aucune bascule nécessaire")
                )
                if evenement.message:
                    journal.append(f"✂ {evenement.message}")

            yield etat(f"{document.nom[:48]} — page {min(faites, total_pages)}")

    archive = None
    if len(produits) > 1:
        chemin_archive = dossier / "markdown.zip"
        with zipfile.ZipFile(chemin_archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for produit in produits:
                zf.write(produit, produit.name)
        archive = str(chemin_archive)

    shutil.rmtree(travail, ignore_errors=True)
    yield (barre(total_pages, total_pages, "terminé"), "\n\n".join(journal),
           lignes, markdown_final, markdown_final,
           [str(p) for p in produits] or None, archive)


def reinitialiser():
    """Vide l'écran pour un nouveau document."""
    return (barre(0, 0, "en attente"), "Prêt.", [], "", "", None, None, None)


def fermer_serveur():
    """Arrête le processus après avoir prévenu le navigateur."""
    def arret() -> None:
        time.sleep(0.8)
        os._exit(0)

    threading.Thread(target=arret, daemon=True).start()
    return gr.update(
        value='<div class="arret">Serveur arrêté. Vous pouvez fermer cet '
              "onglet. Relancez avec ./lancer.sh</div>",
        visible=True,
    )


def construire() -> gr.Blocks:
    with gr.Blocks(title="SNTPK — OCR local", analytics_enabled=False) as interface:
        gr.HTML(_bandeau())
        avancement = gr.HTML(barre(0, 0, "en attente"))
        arret = gr.HTML(visible=False)

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
                deduplication = gr.Checkbox(
                    value=True, label="Retirer en-têtes et pieds répétés",
                    info="Les pages étant converties séparément, ils reviennent "
                         "à chaque page",
                )

        with gr.Row():
            lancer = gr.Button("Convertir", variant="primary", scale=2)
            nouveau = gr.Button("Nouvel OCR", scale=1)
            fermer = gr.Button("Fermer le serveur", variant="stop", scale=1)

        etat = gr.Markdown("Prêt.")
        tableau = gr.Dataframe(
            headers=COLONNES, label="Détail par page", interactive=False,
            wrap=True, column_count=(len(COLONNES), "fixed"),
        )

        with gr.Tabs():
            with gr.Tab("Markdown rendu"):
                rendu = gr.Markdown("")
            with gr.Tab("Source"):
                source = gr.Code(label="", language="markdown", wrap_lines=True)

        with gr.Row():
            fichiers_md = gr.Files(label="Markdown produit", interactive=False)
            archive = gr.File(label="Archive (plusieurs documents)",
                              interactive=False)

        sorties = [avancement, etat, tableau, rendu, source, fichiers_md, archive]

        lancer.click(
            traiter,
            inputs=[entree, seuil, routage, deduplication],
            outputs=sorties,
            concurrency_limit=1,   # MPS et MLX se sérialisent de toute façon
        )
        nouveau.click(reinitialiser, outputs=[*sorties, entree])
        fermer.click(fermer_serveur, outputs=arret)

    return interface


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("docling").setLevel(logging.ERROR)
    favicon = ASSETS / "favicon.png"
    construire().launch(
        theme=THEME,               # police système, jamais gr.themes.GoogleFont
        css=CSS,
        favicon_path=str(favicon) if favicon.exists() else None,
        server_name="127.0.0.1",   # jamais 0.0.0.0 : pas d'écoute sur le réseau
        share=False,               # aucun tunnel public
        ssr_mode=False,            # évite le serveur Node supplémentaire
        mcp_server=False,          # n'expose pas l'app comme serveur MCP
        max_file_size="200mb",
        inbrowser=True,
    )


if __name__ == "__main__":
    main()
