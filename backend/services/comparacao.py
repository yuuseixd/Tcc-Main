"""Comparação entre redes sociais (X x Reddit) para a mesma moeda.

Reaproveita :func:`services.correlacao.calcular_correlacao` — a mesma fonte
de verdade do gráfico e do PDF — e cruza as duas séries horárias:

* **Correlação entre fontes**: Pearson dos Sentiment Scores nas horas em que
  as duas redes têm posts.
* **Concordância**: em quantas dessas horas as duas apontam a mesma direção
  (positiva ou negativa; horas neutras de qualquer lado ficam de fora).
* **Correlação com o preço**: Pearson entre o Sentiment Score de cada fonte e
  a variação percentual de preço da mesma hora.
"""

import math
from sqlalchemy.orm import Session

from services import correlacao as svc_correlacao
from services.sentimento import NEGATIVO, POSITIVO

FONTES_COMPARAVEIS = ("X", "Reddit")

# Abaixo disso a correlação entre fontes é ruído e vira ``None``.
MIN_HORAS_EM_COMUM = 10
# Mínimo de pares para calcular qualquer Pearson.
MIN_PARES_PEARSON = 3


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Correlação de Pearson; ``None`` sem pares suficientes ou sem variância."""
    n = len(xs)
    if n != len(ys) or n < MIN_PARES_PEARSON:
        return None
    media_x = sum(xs) / n
    media_y = sum(ys) / n
    cov = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys))
    var_x = sum((x - media_x) ** 2 for x in xs)
    var_y = sum((y - media_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / math.sqrt(var_x * var_y), 4)


def _correlacao_com_preco(pontos: list[dict]) -> dict:
    pares = [
        (p["sentiment_score"], p["variacao_preco"])
        for p in pontos
        if p.get("variacao_preco") is not None
    ]
    return {
        "pares": len(pares),
        "pearson": pearson([a for a, _ in pares], [b for _, b in pares]),
    }


def _comparar_series(pontos_a: list[dict], pontos_b: list[dict]) -> dict:
    por_hora_a = {p["timestamp"]: p for p in pontos_a}
    por_hora_b = {p["timestamp"]: p for p in pontos_b}
    comuns = sorted(set(por_hora_a) & set(por_hora_b))

    scores_a = [por_hora_a[h]["sentiment_score"] for h in comuns]
    scores_b = [por_hora_b[h]["sentiment_score"] for h in comuns]

    direcoes = [
        (por_hora_a[h]["sentimento_direcao"], por_hora_b[h]["sentimento_direcao"])
        for h in comuns
    ]
    definidas = [
        (da, db_) for da, db_ in direcoes
        if da in (POSITIVO, NEGATIVO) and db_ in (POSITIVO, NEGATIVO)
    ]
    iguais = sum(1 for da, db_ in definidas if da == db_)

    suficiente = len(comuns) >= MIN_HORAS_EM_COMUM
    return {
        "horas_em_comum": len(comuns),
        "correlacao_sentimento": (
            pearson(scores_a, scores_b) if suficiente else None
        ),
        "horas_direcao_definida": len(definidas),
        "concordancia_pct": (
            round(iguais / len(definidas) * 100, 1) if definidas else None
        ),
        "amostra_suficiente": suficiente,
    }


def _serie_alinhada(dados: dict[str, dict], fontes: tuple[str, ...]) -> list[dict]:
    """Uma linha por hora (união das fontes) com o score de cada uma e o preço."""
    horas: dict[str, dict] = {}
    for fonte in fontes:
        for p in dados[fonte]["pontos"]:
            linha = horas.setdefault(
                p["timestamp"],
                {
                    "timestamp": p["timestamp"],
                    "hora": p["hora"],
                    "preco_fechamento": None,
                    "variacao_preco": None,
                    **{f: None for f in fontes},
                },
            )
            linha[fonte] = p["sentiment_score"]
            if linha["preco_fechamento"] is None:
                linha["preco_fechamento"] = p["preco_fechamento"]
                linha["variacao_preco"] = p["variacao_preco"]
    return [horas[k] for k in sorted(horas)]


def comparar_fontes(
    db: Session, moeda: str, fontes: tuple[str, ...] = FONTES_COMPARAVEIS
) -> dict:
    """Resumo por fonte, métricas cruzadas e série horária alinhada."""
    moeda_u = moeda.upper()
    dados = {f: svc_correlacao.calcular_correlacao(db, moeda_u, f) for f in fontes}

    por_fonte = {
        f: {
            "resumo": dados[f]["resumo"],
            "correlacao_preco": _correlacao_com_preco(dados[f]["pontos"]),
            "posts": {
                tipo: sum(p[tipo] for p in dados[f]["pontos"])
                for tipo in ("positivos", "negativos", "neutros")
            },
        }
        for f in fontes
    }

    a, b = fontes[0], fontes[1]
    cruzado = _comparar_series(dados[a]["pontos"], dados[b]["pontos"])
    cruzado["fontes"] = [a, b]
    if not cruzado["amostra_suficiente"]:
        cruzado["aviso"] = (
            f"Só {cruzado['horas_em_comum']} hora(s) com posts nas duas redes "
            f"(mínimo {MIN_HORAS_EM_COMUM}); a correlação entre fontes não é "
            "calculada com amostra tão pequena."
        )

    return {
        "moeda": moeda_u,
        "fontes": por_fonte,
        "comparacao": cruzado,
        "serie": _serie_alinhada(dados, fontes),
    }
