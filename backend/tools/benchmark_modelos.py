"""Compara modelos de análise de sentimento sobre a MESMA base de posts.

Por que existe
--------------
O modelo padrão do projeto (``nlptown/bert-base-multilingual-uncased-sentiment``)
foi treinado em avaliações de produtos, não em texto financeiro. Este script
mede objetivamente o impacto dessa escolha: roda cada modelo candidato sobre os
mesmos posts já coletados, recalcula as métricas de correlação e imprime uma
tabela comparativa.

Como os preços da Binance e o conjunto de posts são idênticos para todos os
modelos, qualquer diferença na taxa de acerto vem exclusivamente do modelo.

Uso
---
    python tools/benchmark_modelos.py                    # todos os modelos
    python tools/benchmark_modelos.py --moeda BTC --fonte X
    python tools/benchmark_modelos.py --modelos ProsusAI/finbert
"""

import argparse
import logging
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import LIMIAR_SENTIMENT_SCORE, LIMIAR_VARIACAO_PRECO_PCT
from db import SessionLocal
from models import SocialPost
from services import correlacao as svc_correlacao
from services import sentimento as svc_sentimento
from utils.tempo import truncar_hora

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# Modelos avaliados. O primeiro é o que o projeto usa hoje (linha de base).
MODELOS = [
    ("nlptown/bert-base-multilingual-uncased-sentiment",
     "Avaliações de produtos, multilíngue (ATUAL)"),
    ("ProsusAI/finbert",
     "Financeiro, treinado em notícias — só inglês"),
    ("cardiffnlp/twitter-xlm-roberta-base-sentiment",
     "Tweets, multilíngue (inclui português)"),
    ("lucas-leme/FinBERT-PT-BR",
     "Financeiro em português"),
]


def intervalo_wilson(acertos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confiança de 95% para a taxa de acerto (método de Wilson).

    Wilson é preferível à aproximação normal simples porque continua válido
    com amostras pequenas — exatamente o caso aqui, onde poucas horas são
    comparáveis. O intervalo mostra a faixa de taxas compatível com os dados.
    """
    if n == 0:
        return (0.0, 0.0)
    p = acertos / n
    denom = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / denom
    margem = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centro - margem) * 100, min(1.0, centro + margem) * 100)


def p_valor_binomial(acertos: int, n: int, p: float = 0.5) -> float | None:
    """Teste binomial exato bilateral contra a hipótese de acerto ao acaso.

    Responde: "se o modelo estivesse chutando (50%), qual a chance de observar
    um resultado tão extremo quanto este?". p > 0.05 significa que o resultado
    é compatível com puro acaso — ou seja, não há evidência de poder preditivo.
    """
    if n == 0:
        return None
    probs = [math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(n + 1)]
    observado = probs[acertos]
    # Soma de todos os desfechos tão ou menos prováveis que o observado.
    return min(1.0, sum(pr for pr in probs if pr <= observado * 1.0000001))


def mapear_label(label: str, id2label: dict | None = None) -> str:
    """Traduz o label de qualquer um dos modelos para o vocabulário do projeto.

    Cada modelo usa uma convenção diferente: estrelas (1–5), positive/negative/
    neutral, ou LABEL_0/1/2. Este mapeamento unifica todas elas.
    """
    texto = str(label).upper()

    # LABEL_0/1/2 — precisa do id2label do modelo para saber o que significam.
    if texto.startswith("LABEL_") and id2label:
        real = id2label.get(int(texto.split("_")[1]), "")
        texto = str(real).upper()

    if "NEUTR" in texto:
        return svc_sentimento.NEUTRO
    if "POS" in texto:
        return svc_sentimento.POSITIVO
    if "NEG" in texto:
        return svc_sentimento.NEGATIVO

    # Formato de estrelas: "1 star" ... "5 stars".
    import re
    m = re.search(r"\d+", texto)
    if m:
        estrelas = int(m.group())
        if estrelas <= 2:
            return svc_sentimento.NEGATIVO
        if estrelas == 3:
            return svc_sentimento.NEUTRO
        return svc_sentimento.POSITIVO

    return svc_sentimento.NEUTRO


def carregar_posts(db, moeda: str, fonte: str) -> list[SocialPost]:
    """Posts que passam no filtro de relevância cripto (independente de modelo)."""
    todos = (
        db.query(SocialPost)
        .filter(SocialPost.moeda == moeda.upper(), SocialPost.fonte == fonte)
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )
    return [p for p in todos
            if svc_sentimento.texto_e_crypto_relevante(p.texto or "")]


def classificar_todos(nome_modelo: str, posts: list[SocialPost]) -> tuple[list[str], float]:
    """Roda o modelo em todos os posts. Devolve (sentimentos, segundos gastos)."""
    from transformers import pipeline

    pipe = pipeline("sentiment-analysis", model=nome_modelo, tokenizer=nome_modelo)
    id2label = getattr(pipe.model.config, "id2label", None)

    inicio = time.time()
    textos = [(p.texto or "")[:2000] for p in posts]
    saidas = pipe(textos, truncation=True, max_length=512, batch_size=8)
    duracao = time.time() - inicio

    return [mapear_label(s["label"], id2label) for s in saidas], duracao


def avaliar(posts: list[SocialPost], sentimentos: list[str],
            precos: dict) -> dict:
    """Recalcula as métricas de correlação com os rótulos de um modelo.

    Reproduz exatamente a regra usada em produção: agrupa por hora, calcula o
    Sentiment Score e compara a direção do sentimento com a do preço.
    """
    horas = defaultdict(lambda: {"pos": 0, "neg": 0, "neu": 0})

    for post, sent in zip(posts, sentimentos):
        chave = truncar_hora(post.timestamp_post).isoformat()
        if sent == svc_sentimento.POSITIVO:
            horas[chave]["pos"] += 1
        elif sent == svc_sentimento.NEGATIVO:
            horas[chave]["neg"] += 1
        else:
            horas[chave]["neu"] += 1

    acertos = erros = comparaveis = 0
    retornos_pos, retornos_neg = [], []

    for chave, c in horas.items():
        total = c["pos"] + c["neg"] + c["neu"]
        score = (c["pos"] - c["neg"]) / total if total else 0

        direcao_sent = (
            svc_sentimento.POSITIVO if score > LIMIAR_SENTIMENT_SCORE
            else svc_sentimento.NEGATIVO if score < -LIMIAR_SENTIMENT_SCORE
            else "neutro"
        )

        info = precos.get(chave)
        if not info:
            continue

        variacao = info["variacao_pct"]
        direcao_preco = (
            "subiu" if variacao > LIMIAR_VARIACAO_PRECO_PCT
            else "desceu" if variacao < -LIMIAR_VARIACAO_PRECO_PCT
            else "estavel"
        )

        if direcao_sent == svc_sentimento.POSITIVO:
            retornos_pos.append(variacao)
        elif direcao_sent == svc_sentimento.NEGATIVO:
            retornos_neg.append(variacao)

        if direcao_sent != "neutro" and direcao_preco != "estavel":
            comparaveis += 1
            if ((direcao_sent == svc_sentimento.POSITIVO and direcao_preco == "subiu")
                    or (direcao_sent == svc_sentimento.NEGATIVO and direcao_preco == "desceu")):
                acertos += 1
            else:
                erros += 1

    media = lambda xs: round(sum(xs) / len(xs), 4) if xs else None

    return {
        "horas": len(horas),
        "comparaveis": comparaveis,
        "acertos": acertos,
        "erros": erros,
        "taxa": round(acertos / comparaveis * 100, 1) if comparaveis else None,
        "positivos": sentimentos.count(svc_sentimento.POSITIVO),
        "negativos": sentimentos.count(svc_sentimento.NEGATIVO),
        "neutros": sentimentos.count(svc_sentimento.NEUTRO),
        "ret_pos": media(retornos_pos),
        "ret_neg": media(retornos_neg),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--moeda", default="BTC")
    parser.add_argument("--fonte", default="X")
    parser.add_argument("--modelos", nargs="*", help="Subconjunto a testar")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        posts = carregar_posts(db, args.moeda, args.fonte)
        if not posts:
            print(f"Nenhum post de {args.moeda}/{args.fonte} no banco.")
            return

        print("=" * 78)
        print(f"  BENCHMARK DE MODELOS — {args.moeda}/{args.fonte}")
        print("=" * 78)
        print(f"  Posts relevantes: {len(posts)}")
        print(f"  Periodo: {posts[0].timestamp_post:%d/%m/%Y} a "
              f"{posts[-1].timestamp_post:%d/%m/%Y}")

        horas_unicas = {
            truncar_hora(p.timestamp_post).isoformat() for p in posts
        }
        precos = svc_correlacao.precos_para_horas(args.moeda, list(horas_unicas))
        print(f"  Horas com preco disponivel: {len(precos)}")
        print()

        candidatos = [
            (nome, desc) for nome, desc in MODELOS
            if not args.modelos or nome in args.modelos
        ]

        resultados = []
        for nome, descricao in candidatos:
            print(f"  Carregando {nome} ...", flush=True)
            try:
                sentimentos, duracao = classificar_todos(nome, posts)
            except Exception as e:
                print(f"    FALHOU: {e}\n")
                continue

            r = avaliar(posts, sentimentos, precos)
            r["modelo"] = nome
            r["descricao"] = descricao
            r["segundos"] = round(duracao, 1)
            resultados.append(r)
            print(f"    concluido em {duracao:.1f}s\n", flush=True)

        if not resultados:
            print("Nenhum modelo pode ser avaliado.")
            return

        # ── Tabela comparativa ──────────────────────────────────────────
        print("=" * 78)
        print("  RESULTADOS")
        print("=" * 78)
        print()
        print(f"  {'Modelo':<32} {'Taxa':>7} {'n':>4} {'IC 95%':>18} {'p':>7}  Veredito")
        print("  " + "-" * 92)
        for r in sorted(resultados, key=lambda x: x["taxa"] or 0, reverse=True):
            taxa = f"{r['taxa']}%" if r["taxa"] is not None else "N/A"
            lo, hi = intervalo_wilson(r["acertos"], r["comparaveis"])
            p = p_valor_binomial(r["acertos"], r["comparaveis"])
            ic = f"[{lo:.1f}%, {hi:.1f}%]"
            veredito = (
                "indistinguivel do acaso" if p is None or p > 0.05
                else "DIFERE do acaso"
            )
            p_txt = f"{p:.3f}" if p is not None else "N/A"
            print(f"  {r['modelo'][:30]:<32} {taxa:>7} {r['comparaveis']:>4} "
                  f"{ic:>18} {p_txt:>7}  {veredito}")
        print()

        print("  Distribuicao dos rotulos e retorno medio (1h):")
        print()
        for r in resultados:
            print(f"  {r['modelo']}")
            print(f"    {r['descricao']}")
            print(f"    positivos={r['positivos']:<4} negativos={r['negativos']:<4} "
                  f"neutros={r['neutros']:<4}  ({r['segundos']}s)")
            rp = f"{r['ret_pos']:+.4f}%" if r["ret_pos"] is not None else "sem dados"
            rn = f"{r['ret_neg']:+.4f}%" if r["ret_neg"] is not None else "sem dados"
            print(f"    retorno apos hora POSITIVA: {rp}")
            print(f"    retorno apos hora NEGATIVA: {rn}")
            print()

        # ── Leitura dos resultados ──────────────────────────────────────
        print("  Como ler:")
        print("    IC 95%  faixa de taxas compativel com os dados. Se conter 50%,")
        print("            o modelo nao se distingue de um chute.")
        print("    p       chance de observar este resultado se o modelo chutasse.")
        print("            p > 0.05 = sem evidencia de poder preditivo.")
        print()

        significativos = [
            r for r in resultados
            if (p_valor_binomial(r["acertos"], r["comparaveis"]) or 1) <= 0.05
        ]
        maior_n = max(r["comparaveis"] for r in resultados)

        if not significativos:
            print("  CONCLUSAO: nenhum modelo difere estatisticamente do acaso.")
            print(f"  A maior amostra comparavel tem apenas {maior_n} horas, o que")
            print("  torna impossivel distinguir os modelos entre si ou afirmar que")
            print("  qualquer um deles preve a direcao do preco.")
            print("  O caminho e coletar mais dados, nao trocar de modelo.")
        else:
            melhor = max(significativos, key=lambda r: r["taxa"])
            print(f"  CONCLUSAO: {melhor['modelo']} difere do acaso "
                  f"(taxa {melhor['taxa']}%, n={melhor['comparaveis']}).")
        print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
