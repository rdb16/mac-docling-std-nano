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
from mac_docling.moteur import ORDRE_NOTES, Config, Genre, Moteur, convertir  # noqa: E402

_log = logging.getLogger(__name__)

ASSETS = Path(__file__).parent / "assets"
MOTEUR = Moteur()

COLONNES = ["Page", "Moteur", "parse", "layout", "OCR", "tableaux",
            "total", "confiance", "note", "bascule"]

# Les deux gammes sont relevées sur le logo : le turquoise du cerveau-circuit
# et le graphite du cadre. Les échantillons cités en commentaire sont les
# pixels d'origine, les autres paliers sont interpolés autour.
TURQUOISE = gr.themes.Color(
    c50="#eefbfa", c100="#d2f4f2", c200="#a8e8e7",
    c300="#74d9d8", c400="#3cbfc1",
    c500="#1fa2a6",   # cœur du halo, proche de #2b7671 éclairci
    c600="#158286", c700="#16696d",
    c800="#175457", c900="#17464a", c950="#082a2d",
)
GRAPHITE = gr.themes.Color(
    c50="#f6f7f7", c100="#ebeded", c200="#d8dadb",
    c300="#b6bbbb", c400="#8d9293",
    c500="#6c7172", c600="#555959", c700="#434647",
    c800="#2c2e2f",
    c900="#202123",   # teinte exacte du cadre du logo
    c950="#141517",
)

# Police système : gr.themes.GoogleFont ferait charger fonts.googleapis.com
# depuis le navigateur.
THEME = gr.themes.Soft(
    primary_hue=TURQUOISE,
    secondary_hue=TURQUOISE,
    neutral_hue=GRAPHITE,
    radius_size=gr.themes.sizes.radius_md,
    font=["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
    font_mono=["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
).set(
    # Fond très légèrement turquoise en clair, graphite du logo en sombre.
    body_background_fill="#f4f8f8",
    body_background_fill_dark=GRAPHITE.c950,
    background_fill_secondary="#eaf2f2",
    background_fill_secondary_dark=GRAPHITE.c900,
    block_background_fill="white",
    block_background_fill_dark=GRAPHITE.c900,
    block_border_width="1px",
    border_color_primary="#d6e4e4",
    border_color_primary_dark=GRAPHITE.c800,
    block_label_background_fill=TURQUOISE.c50,
    block_label_background_fill_dark=TURQUOISE.c800,
    block_label_text_color=TURQUOISE.c700,
    block_label_text_color_dark=TURQUOISE.c100,
    block_title_text_color=TURQUOISE.c700,
    block_title_text_color_dark=TURQUOISE.c100,
    block_shadow="0 1px 2px rgba(23, 70, 74, .06)",
    button_large_radius="10px",
    button_medium_radius="10px",
    # Le dégradé n'est pas hérité en sombre : Gradio y retombe sur *primary_600.
    button_primary_background_fill=f"linear-gradient(135deg, {TURQUOISE.c600}, {TURQUOISE.c400})",
    button_primary_background_fill_dark=f"linear-gradient(135deg, {TURQUOISE.c700}, {TURQUOISE.c500})",
    button_primary_background_fill_hover=f"linear-gradient(135deg, {TURQUOISE.c500}, {TURQUOISE.c300})",
    button_primary_background_fill_hover_dark=f"linear-gradient(135deg, {TURQUOISE.c600}, {TURQUOISE.c400})",
    button_primary_border_color=TURQUOISE.c600,
    button_primary_border_color_dark=TURQUOISE.c700,
    button_primary_shadow="0 2px 8px rgba(21, 130, 134, .28)",
    button_primary_shadow_dark="0 2px 10px rgba(8, 42, 45, .55)",
    button_secondary_background_fill="white",
    button_secondary_background_fill_dark=GRAPHITE.c800,
    button_secondary_background_fill_hover=TURQUOISE.c50,
    button_secondary_background_fill_hover_dark=TURQUOISE.c800,
    button_secondary_border_color="#cadcdc",
    button_secondary_border_color_dark=GRAPHITE.c700,
    button_secondary_text_color=GRAPHITE.c700,
    button_secondary_text_color_dark=GRAPHITE.c100,
    checkbox_background_color_selected=TURQUOISE.c600,
    checkbox_border_color_selected=TURQUOISE.c600,
    checkbox_border_color_focus=TURQUOISE.c400,
    checkbox_label_background_fill_selected=TURQUOISE.c50,
    checkbox_label_background_fill_selected_dark=TURQUOISE.c800,
    input_border_color_focus=TURQUOISE.c400,
    slider_color=TURQUOISE.c500,
    table_border_color=TURQUOISE.c100,
    table_border_color_dark=GRAPHITE.c800,
    table_even_background_fill="#f3f9f9",
    table_even_background_fill_dark=GRAPHITE.c800,
    table_odd_background_fill="white",
    table_odd_background_fill_dark=GRAPHITE.c900,
    link_text_color=TURQUOISE.c700,
    link_text_color_dark=TURQUOISE.c300,
    link_text_color_hover=TURQUOISE.c500,
    link_text_color_hover_dark=TURQUOISE.c200,
)

CSS = """
footer { display: none !important; }

/* Bandeau : le logo est posé sur un panneau graphite, comme son propre
   cadre, avec le halo turquoise du cerveau-circuit prolongé derrière. */
#bandeau {
  display: flex; align-items: center; gap: 18px;
  padding: 16px 20px; margin-bottom: 10px; border-radius: 14px;
  background:
    radial-gradient(120% 180% at 12% 50%, rgba(31, 162, 166, .30), transparent 60%),
    linear-gradient(135deg, #202123 0%, #171a1b 55%, #11272a 100%);
  border: 1px solid #2f3a3b;
  box-shadow: 0 6px 22px rgba(8, 42, 45, .28);
}
#bandeau img {
  width: 54px; height: 54px; border-radius: 12px; flex: none;
  box-shadow: 0 0 0 1px rgba(116, 217, 216, .45),
              0 0 18px rgba(31, 162, 166, .40);
}
#bandeau h1 {
  margin: 0; font-size: 21px; line-height: 1.2; font-weight: 600;
  color: #eaf5f4; letter-spacing: .02em;
}
#bandeau p {
  margin: 4px 0 0; font-size: 13px; line-height: 1.45;
  color: #9fc4c4; opacity: 1;
}
#bandeau p b { color: #74d9d8; font-weight: 600; }

/* Barre d'avancement : même dégradé que le bouton primaire. */
.piste {
  height: 9px; border-radius: 99px; overflow: hidden;
  background: var(--neutral-200); margin: 10px 0 5px;
  box-shadow: inset 0 1px 2px rgba(8, 42, 45, .10);
}
.jauge {
  height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, var(--primary-600), var(--primary-400));
  transition: width .25s ease;
}
.jauge.finie {
  background: linear-gradient(90deg, var(--primary-500), var(--primary-300));
  box-shadow: 0 0 12px rgba(60, 191, 193, .55);
}
.legende {
  font-size: 12px; display: flex; justify-content: space-between;
  color: var(--primary-700); letter-spacing: .01em;
}
.dark .legende { color: var(--primary-300); }

/* Onglets et étiquettes reprennent le turquoise plutôt que le bleu Gradio. */
.tab-nav button.selected {
  color: var(--primary-600) !important;
  border-bottom-color: var(--primary-500) !important;
}
.dark .tab-nav button.selected { color: var(--primary-300) !important; }
table thead th {
  background: var(--primary-50) !important;
  color: var(--primary-800) !important;
  font-weight: 600;
}
.dark table thead th {
  background: var(--primary-900) !important;
  color: var(--primary-100) !important;
}

/* Le panneau d'arrêt garde le rouge d'alerte, adouci au graphite ambiant. */
.arret {
  padding: 15px; border-radius: 12px; text-align: center; font-weight: 600;
  background: #fdf3f3; color: #9a2b2b; border: 1px solid #f0d6d6;
}
.dark .arret {
  background: #2a1d1e; color: #f0b4b4; border-color: #4a2c2e;
}
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
        "Nanonets-OCR2 page par page · <b>rien ne quitte cette machine</b></p>"
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
    moteur = "standard" if donnees["config"] == Config.STANDARD else "Nanonets"
    if donnees["config"] == Config.NANONETS:
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

            if evenement.genre == Genre.DOCUMENT_DEBUT:
                extra = " · image normalisée" if donnees.get("normalise") else ""
                journal.append(f"{entete}\n{evenement.message}{extra}")

            elif evenement.genre == Genre.MODELE:
                duree = f" ({_ms(donnees.get('ms'))})" if donnees.get("ms") else ""
                journal.append(f"· {evenement.message}{duree}")

            elif evenement.genre == Genre.PAGE_FIN:
                lignes = lignes + [_ligne_page(donnees)]
                # L'avancement compte les pages, pas les passages : une page
                # repassée au VLM ne la fait pas avancer deux fois.
                if donnees["config"] == Config.STANDARD:
                    numero = donnees["page"]
                    if numero not in vues_standard:
                        vues_standard.add(numero)
                        faites += 1
                if donnees.get("alerte"):
                    journal.append(f"⚠ page {donnees['page']} : {donnees['alerte']}")

            elif evenement.genre == Genre.PAGE_BASCULE:
                journal.append(f"⚡ **{evenement.message}**")

            elif evenement.genre == Genre.ERREUR:
                journal.append(f"✗ {evenement.message}")

            elif evenement.genre == Genre.DOCUMENT_FIN:
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
