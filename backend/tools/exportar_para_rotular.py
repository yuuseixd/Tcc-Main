"""Sorteia posts do banco e grava uma planilha para rotulagem manual.

Por que existe
--------------
Para saber se um modelo de sentimento é bom, é preciso comparar o que ele diz
com o sentimento *correto* de cada post. Este script monta a planilha em que
duas pessoas classificam os posts, sem ver a opinião de nenhum modelo (para não
influenciar o rótulo).

Uso
---
    python tools/exportar_para_rotular.py                  # 150 posts
    python tools/exportar_para_rotular.py --n 200 --seed 7

Como rotular
------------
Abra o .xlsx no Excel. Cada pessoa preenche UMA coluna, sem olhar a da outra:
``rotulo_a`` e ``rotulo_b``, escolhendo na lista positivo, neutro ou negativo.

* positivo: o texto é favorável ao preço/mercado (alta, ganhos, otimismo).
* negativo: o texto é desfavorável (queda, perdas, medo, golpe).
* neutro: informativo, sem direção clara, ou pergunta.

Em dúvida, escolha neutro. Salve mantendo o formato .xlsx.
"""

import argparse
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import SessionLocal
from models import SocialPost
from services.sentimento import texto_e_crypto_relevante

PASTA = Path(__file__).resolve().parent.parent / "rotulagem"
CABECALHO = ["id", "fonte", "autor", "texto", "rotulo_a", "rotulo_b"]
INSTRUCOES = [
    "Como rotular",
    "Pessoa 1 preenche a coluna rotulo_a e pessoa 2 a rotulo_b, SEM olhar a do outro.",
    "Clique na célula e escolha na lista: positivo, neutro ou negativo.",
    "positivo: favorável ao preço ou ao mercado (alta, ganhos, otimismo).",
    "negativo: desfavorável (queda, perdas, medo, golpe).",
    "neutro: informativo, sem direção clara, ou pergunta. Em dúvida, escolha neutro.",
    "Salve o arquivo (Ctrl+S) mantendo o formato .xlsx.",
]


def gravar_xlsx(caminho: Path, linhas: list[list]) -> None:
    """Planilha pronta para rotular: colunas largas, texto quebrado e lista suspensa."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Posts"
    ws.append(CABECALHO)
    for linha in linhas:
        ws.append(linha)

    for col, largura in zip("ABCDEF", (7, 10, 22, 95, 14, 14)):
        ws.column_dimensions[col].width = largura
    cabecalho = PatternFill("solid", fgColor="DDE6F5")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = cabecalho
    ws.freeze_panes = "A2"
    for linha in ws.iter_rows(min_row=2):
        for c in linha:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ultima = len(linhas) + 1
    lista = DataValidation(
        type="list", formula1='"positivo,neutro,negativo"', allow_blank=True,
        showErrorMessage=True, errorTitle="Rótulo inválido",
        error="Escolha positivo, neutro ou negativo.",
    )
    ws.add_data_validation(lista)
    lista.add(f"E2:F{ultima}")
    for linha in ws[f"E2:F{ultima}"]:
        for c in linha:
            c.fill = PatternFill("solid", fgColor="FFF8DC")

    guia = wb.create_sheet("Instruções")
    guia.column_dimensions["A"].width = 100
    for i, texto in enumerate(INSTRUCOES, start=1):
        guia.cell(row=i, column=1, value=texto).alignment = Alignment(wrap_text=True)
    guia["A1"].font = Font(bold=True, size=13)
    wb.save(caminho)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=150, help="posts a sortear")
    parser.add_argument("--seed", type=int, default=42, help="semente do sorteio")
    parser.add_argument(
        "--saida", default=str(PASTA / "posts_para_rotular.xlsx"),
        help="arquivo de saída (.xlsx ou .csv)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        posts = db.query(SocialPost).all()
    finally:
        db.close()

    relevantes = [p for p in posts if texto_e_crypto_relevante(p.texto or "")]
    por_fonte: dict[str, list[SocialPost]] = {}
    for p in relevantes:
        por_fonte.setdefault(p.fonte, []).append(p)

    # Divide o sorteio entre as fontes (metade e metade) para que a base
    # represente as duas redes; se uma tiver poucos posts, a outra completa.
    rng = random.Random(args.seed)
    escolhidos: list[SocialPost] = []
    fontes = sorted(por_fonte)
    sobra = args.n
    for i, fonte in enumerate(fontes):
        cota = sobra // (len(fontes) - i)
        lote = por_fonte[fonte][:]
        rng.shuffle(lote)
        escolhidos.extend(lote[:cota])
        sobra -= min(cota, len(lote))
    rng.shuffle(escolhidos)

    saida = Path(args.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    linhas = [
        [p.id, p.fonte, p.autor or "", " ".join((p.texto or "").split()), "", ""]
        for p in escolhidos
    ]
    if saida.suffix.lower() == ".xlsx":
        gravar_xlsx(saida, linhas)
    else:
        with open(saida, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(CABECALHO)
            w.writerows(linhas)

    contagem = {f: sum(1 for p in escolhidos if p.fonte == f) for f in fontes}
    print(f"{len(escolhidos)} posts gravados em {saida}")
    print(f"Por fonte: {contagem} (de {len(relevantes)} posts relevantes no banco)")
    print("Preencha rotulo_a (pessoa 1) e rotulo_b (pessoa 2) com "
          "positivo, neutro ou negativo.")


if __name__ == "__main__":
    main()
