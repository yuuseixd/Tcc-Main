"""Geração dos relatórios PDF do SentCrypto.

Texto e Unicode
---------------
As fontes nativas do PDF (Helvetica) só cobrem latin-1. O código antigo fazia
``texto.encode("latin-1", errors="replace")``, o que transformava todo acento
e emoji de tweet em ``?`` — ruim para um relatório que vai anexo ao TCC.
Aqui tentamos registrar uma fonte TrueType Unicode do sistema; se não houver,
o texto é transliterado de forma legível em vez de virar interrogação.
"""

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

from config import RELATORIOS_DIR
from services.sentimento import NEGATIVO, NULO, POSITIVO

logger = logging.getLogger("sentcrypto.relatorios")

RELATORIOS_DIR.mkdir(parents=True, exist_ok=True)

# Paleta alinhada com o dashboard.
COR_FUNDO_CABECALHO = (15, 23, 42)
COR_BRANCO = (255, 255, 255)
COR_PRETO = (0, 0, 0)
COR_POSITIVO = (22, 163, 74)
COR_NEGATIVO = (220, 38, 38)
COR_NEUTRO = (161, 98, 7)
COR_NULO = (148, 163, 184)
COR_CINZA = (148, 163, 184)

# Fontes Unicode procuradas no sistema, em ordem de preferência.
_CANDIDATOS_FONTE = [
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
]
_CANDIDATOS_FONTE_BOLD = {
    "segoeui.ttf": "seguisb.ttf",
    "arial.ttf": "arialbd.ttf",
    "DejaVuSans.ttf": "DejaVuSans-Bold.ttf",
    "Arial.ttf": "Arial Bold.ttf",
}

# Emojis e símbolos fora do plano básico não existem em nenhuma fonte de texto.
_FORA_DO_BMP = re.compile(r"[\U00010000-\U0010FFFF]")


def _sanitizar(texto: str, unicode_ok: bool) -> str:
    """Deixa o texto renderizável pela fonte ativa.

    Com fonte Unicode, basta remover emojis (fora do BMP). Sem ela, remove
    acentos preservando a letra base ("ação" -> "acao") em vez de "a??o".
    """
    if not texto:
        return ""

    texto = _FORA_DO_BMP.sub("", texto)
    texto = texto.replace("\r", " ").replace("\n", " ").strip()

    if unicode_ok:
        return texto

    normalizado = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in normalizado if not unicodedata.combining(c))
    return sem_acento.encode("latin-1", errors="replace").decode("latin-1")


class SentCryptoPDF(FPDF):
    """PDF com o cabeçalho e rodapé padrão do projeto."""

    def __init__(self, subtitulo: str = ""):
        super().__init__()
        self.subtitulo = subtitulo
        self.unicode_ok = False
        self.fonte = "Helvetica"
        self._registrar_fonte_unicode()
        self.alias_nb_pages()
        self.set_auto_page_break(auto=True, margin=20)

    def _registrar_fonte_unicode(self) -> None:
        """Registra uma fonte TTF do sistema para suportar acentuação."""
        for caminho in _CANDIDATOS_FONTE:
            if not caminho.exists():
                continue
            try:
                self.add_font("Texto", "", str(caminho))
                bold = caminho.parent / _CANDIDATOS_FONTE_BOLD.get(caminho.name, "")
                if bold.exists():
                    self.add_font("Texto", "B", str(bold))
                else:
                    # Sem arquivo bold, reaproveita o regular para não quebrar.
                    self.add_font("Texto", "B", str(caminho))
                self.fonte = "Texto"
                self.unicode_ok = True
                logger.info("Fonte Unicode do PDF: %s", caminho.name)
                return
            except Exception as e:
                logger.debug("Falha ao registrar fonte %s: %s", caminho, e)

        logger.info("Nenhuma fonte Unicode encontrada; PDF usará Helvetica.")

    def txt(self, texto: str) -> str:
        """Sanitiza um texto para a fonte ativa deste documento."""
        return _sanitizar(texto, self.unicode_ok)

    def header(self):
        self.set_fill_color(*COR_FUNDO_CABECALHO)
        self.rect(0, 0, self.w, 32, "F")
        self.set_text_color(*COR_BRANCO)
        self.set_font(self.fonte, "B", 16)
        self.cell(
            0, 12, self.txt("SentCrypto — Relatório de Sentimento"),
            new_x="LMARGIN", new_y="NEXT",
        )
        if self.subtitulo:
            self.set_font(self.fonte, "", 9)
            self.cell(0, 8, self.txt(self.subtitulo), new_x="LMARGIN", new_y="NEXT")
        self.ln(14)
        self.set_text_color(*COR_PRETO)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.fonte, "", 7)
        self.set_text_color(*COR_CINZA)
        rodape = (
            f"Gerado por SentCrypto · {datetime.now():%d/%m/%Y %H:%M:%S}"
            f"   |   Pág. {self.page_no()}/{{nb}}"
        )
        self.cell(0, 10, self.txt(rodape), align="C")

    # ── Blocos reutilizáveis ────────────────────────────────────────────

    def titulo_secao(self, texto: str, tamanho: int = 13) -> None:
        self.set_text_color(*COR_PRETO)
        self.set_font(self.fonte, "B", tamanho)
        self.cell(0, 10, self.txt(texto), new_x="LMARGIN", new_y="NEXT")

    def paragrafo(self, linhas: list[str], altura: int = 5) -> None:
        self.set_font(self.fonte, "", 10)
        self.set_text_color(*COR_PRETO)
        for linha in linhas:
            self.cell(0, altura, self.txt(linha), new_x="LMARGIN", new_y="NEXT")

    def cabecalho_tabela(self, larguras: list[float], titulos: list[str],
                         tamanho: int = 8) -> None:
        self.set_font(self.fonte, "B", tamanho)
        self.set_fill_color(*COR_FUNDO_CABECALHO)
        self.set_text_color(*COR_BRANCO)
        for largura, titulo in zip(larguras, titulos):
            self.cell(largura, 7, self.txt(titulo), border=1, fill=True, align="C")
        self.ln()
        self.set_text_color(*COR_PRETO)
        self.set_font(self.fonte, "", tamanho)


def _cor_do_sentimento(sentimento: str | None) -> tuple[int, int, int]:
    if sentimento == POSITIVO:
        return COR_POSITIVO
    if sentimento == NEGATIVO:
        return COR_NEGATIVO
    if sentimento == NULO:
        return COR_NULO
    return COR_NEUTRO


# ═══════════════════════════════════════════════════════════════════════════
#  Relatório 1 — posts de uma hora específica
# ═══════════════════════════════════════════════════════════════════════════


def gerar_relatorio_posts(
    moeda: str,
    fonte: str,
    hora: datetime,
    posts: list,
    indice: float | None = None,
) -> Path:
    """Monta o PDF com todos os posts de uma hora e devolve o caminho salvo."""
    total = len(posts)
    relevantes = [p for p in posts if p.sentimento != NULO]
    positivos = sum(1 for p in relevantes if p.sentimento == POSITIVO)
    negativos = sum(1 for p in relevantes if p.sentimento == NEGATIVO)
    neutros = len(relevantes) - positivos - negativos
    nulos = total - len(relevantes)

    pdf = SentCryptoPDF(
        subtitulo=f"{moeda.upper()}/USDT  ·  {fonte}  ·  {hora:%d/%m/%Y %H:%M} UTC"
    )
    pdf.add_page()

    # ── Resumo ──
    pdf.titulo_secao("Resumo")
    indice_str = f"{indice * 100:.1f}%" if indice is not None else "N/A"
    pdf.paragrafo(
        [
            f"Total de posts coletados: {total}",
            f"Positivos: {positivos}   |   Negativos: {negativos}   |   "
            f"Neutros: {neutros}   |   Não-cripto: {nulos}",
            f"Índice médio de sentimento: {indice_str}",
        ],
        altura=6,
    )
    pdf.ln(6)

    if not posts:
        pdf.paragrafo(["Nenhum post encontrado para esta hora."])
        return _salvar(pdf, f"sentcrypto_{moeda.upper()}_{fonte}_{hora:%Y%m%d_%Hh}.pdf")

    # ── Tabela ──
    larguras = [10, 95, 25, 22, 18]
    titulos = ["#", "Texto", "Sentimento", "Score", "Hora"]
    altura_linha = 4
    pdf.cabecalho_tabela(larguras, titulos, tamanho=8)
    pdf.set_font(pdf.fonte, "", 7)

    # O controle de quebra é manual: a quebra automática no meio de uma linha
    # de tabela desalinharia as bordas das colunas.
    pdf.set_auto_page_break(auto=False)

    largura_texto = larguras[1]
    for i, p in enumerate(posts, 1):
        texto = pdf.txt((p.texto or "")[:300])
        if p.texto and len(p.texto) > 300:
            texto += "..."

        linhas = pdf.multi_cell(
            largura_texto, altura_linha, texto or "-",
            dry_run=True, output="LINES",
        )
        # A altura da linha é múltiplo exato de altura_linha para que a coluna
        # de texto e as demais colunas terminem na mesma altura.
        n_linhas = max(2, len(linhas) or 1)
        altura_row = n_linhas * altura_linha

        if pdf.get_y() + altura_row > pdf.h - 20:
            pdf.add_page()
            pdf.cabecalho_tabela(larguras, titulos, tamanho=8)
            pdf.set_font(pdf.fonte, "", 7)

        x0, y0 = pdf.get_x(), pdf.get_y()

        pdf.set_text_color(*COR_PRETO)
        pdf.cell(larguras[0], altura_row, str(i), border=1, align="C")

        pdf.set_xy(x0 + larguras[0], y0)
        pdf.multi_cell(larguras[1], altura_linha, texto or "-", border=1)

        pdf.set_text_color(*_cor_do_sentimento(p.sentimento))
        pdf.set_xy(x0 + sum(larguras[:2]), y0)
        pdf.cell(larguras[2], altura_row, p.sentimento or "-", border=1, align="C")

        pdf.set_text_color(*COR_PRETO)
        score_str = f"{p.score:.4f}" if p.score is not None else "-"
        pdf.set_xy(x0 + sum(larguras[:3]), y0)
        pdf.cell(larguras[3], altura_row, score_str, border=1, align="C")

        hora_post = f"{p.timestamp_post:%H:%M}" if p.timestamp_post else "-"
        pdf.set_xy(x0 + sum(larguras[:4]), y0)
        pdf.cell(larguras[4], altura_row, hora_post, border=1, align="C")

        pdf.set_xy(x0, y0 + altura_row)

    nome = f"sentcrypto_{moeda.upper()}_{fonte}_{hora:%Y%m%d_%Hh}.pdf"
    return _salvar(pdf, nome)


# ═══════════════════════════════════════════════════════════════════════════
#  Relatório 2 — correlação sentimento x preço
# ═══════════════════════════════════════════════════════════════════════════


def gerar_relatorio_correlacao(dados: dict) -> Path:
    """Monta o PDF de correlação a partir do resultado de ``calcular_correlacao``."""
    moeda = dados["moeda"]
    fonte = dados["fonte"]
    pontos = dados["pontos"]
    resumo = dados["resumo"]

    pdf = SentCryptoPDF(
        subtitulo=f"{moeda}/USDT  ·  {fonte}  ·  Correlação Sentimento x Preço"
    )
    pdf.add_page()

    # ── 1. Sentiment Score ──
    pdf.titulo_secao("1. Sentiment Score (força do sentimento)", tamanho=14)
    pdf.paragrafo([
        "Resume o humor do mercado em cada hora num único número.",
        "",
        "Fórmula:  SentimentScore = (Positivos - Negativos) / Total",
        "",
        "Escala:  +1 = extremamente positivo  |  0 = neutro  |  -1 = extremamente negativo",
        "",
        f"Score médio no período analisado: {resumo['score_medio']:+.4f}",
        f"Horas com posts: {resumo['total_horas_analisadas']}   |   "
        f"Posts considerados: {resumo['total_posts']}",
    ])
    pdf.ln(4)

    # ── 2. Return After Sentiment ──
    pdf.titulo_secao("2. Return After Sentiment (impacto no preço)", tamanho=14)
    ret_pos = resumo["retorno_medio_apos_positivo"]
    ret_neg = resumo["retorno_medio_apos_negativo"]
    pdf.paragrafo([
        "Mede o retorno da moeda nas horas seguintes ao sentimento observado.",
        "",
        "Fórmula:  Return = ((Preço_t+n - Preço_t) / Preço_t) x 100",
        "",
        "Janelas utilizadas: 1h, 4h e 24h.",
        "",
        f"Retorno médio em 1h após hora POSITIVA: "
        f"{f'{ret_pos:+.4f}%' if ret_pos is not None else 'sem dados'}",
        f"Retorno médio em 1h após hora NEGATIVA: "
        f"{f'{ret_neg:+.4f}%' if ret_neg is not None else 'sem dados'}",
    ])
    pdf.ln(4)

    # ── 3. Resumo da correlação ──
    pdf.titulo_secao("3. Resumo da Correlação", tamanho=14)
    taxa = resumo["taxa_acerto_pct"]
    linhas = [
        f"Horas comparáveis (sentimento não-neutro e preço não-estável): "
        f"{resumo['total_comparavel']}",
        f"Acertos (o sentimento antecipou a direção do preço): {resumo['acertos']}",
        f"Erros (o sentimento não correspondeu):                {resumo['erros']}",
        f"Taxa de acerto: {f'{taxa}%' if taxa is not None else 'N/A (sem dados comparáveis)'}",
    ]
    if not resumo["amostra_suficiente"]:
        linhas += [
            "",
            "Ressalva metodológica: a amostra comparável é menor que 30 horas.",
            "A taxa de acerto acima é indicativa e não sustenta conclusão",
            "estatística; colete mais dados antes de interpretá-la.",
        ]
    pdf.paragrafo(linhas, altura=6)
    pdf.ln(6)

    # ── 4. Tabela detalhada ──
    if not pontos:
        pdf.paragrafo(["Nenhuma hora com posts relevantes para detalhar."])
        return _salvar(
            pdf,
            f"sentcrypto_{moeda}_{fonte}_correlacao_{datetime.now():%Y%m%d_%Hh%M}.pdf",
        )

    pdf.titulo_secao(f"4. Detalhamento por Hora ({len(pontos)} horas)")
    pdf.ln(2)

    larguras = [26, 14, 12, 12, 24, 20, 20, 20, 18]
    titulos = ["Hora (UTC)", "Score", "Pos", "Neg", "Preço", "Var.1h",
               "Ret.1h", "Ret.4h", "Result."]
    pdf.cabecalho_tabela(larguras, titulos, tamanho=7)
    pdf.set_auto_page_break(auto=False)

    for p in pontos:
        if pdf.get_y() + 6 > pdf.h - 20:
            pdf.add_page()
            pdf.cabecalho_tabela(larguras, titulos, tamanho=7)

        if p["acertou"] is True:
            pdf.set_text_color(*COR_POSITIVO)
        elif p["acertou"] is False:
            pdf.set_text_color(*COR_NEGATIVO)
        else:
            pdf.set_text_color(60, 60, 60)

        valores = [
            p["hora"],
            f"{p['sentiment_score']:+.2f}",
            str(p["positivos"]),
            str(p["negativos"]),
            f"${p['preco_abertura']:,.0f}" if p["preco_abertura"] else "-",
            _pct(p["variacao_preco"]),
            _pct(p["retorno_1h"]),
            _pct(p["retorno_4h"]),
            "Acerto" if p["acertou"] is True else "Erro" if p["acertou"] is False else "-",
        ]
        for largura, valor in zip(larguras, valores):
            pdf.cell(largura, 6, pdf.txt(valor), border=1, align="C")
        pdf.ln()

    pdf.set_text_color(*COR_PRETO)

    nome = f"sentcrypto_{moeda}_{fonte}_correlacao_{datetime.now():%Y%m%d_%Hh%M}.pdf"
    return _salvar(pdf, nome)


def _pct(valor: float | None) -> str:
    return f"{valor:+.3f}%" if valor is not None else "-"


def _salvar(pdf: FPDF, nome_arquivo: str) -> Path:
    caminho = RELATORIOS_DIR / nome_arquivo
    pdf.output(str(caminho))
    logger.info("Relatório PDF salvo: %s", caminho)
    return caminho
