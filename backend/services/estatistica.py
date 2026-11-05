"""Testes estatísticos da correlação sentimento x preço.

Uma taxa de acerto sozinha não diz nada: 55% em 20 horas é indistinguível de
jogar moeda, e 60% num período em que o preço subiu 60% das horas é só o
mercado subindo. Este módulo responde às três perguntas que tornam o número
defensável:

1. **É diferente do acaso?** Teste binomial exato contra 50% e intervalo de
   confiança de Wilson (95%).
2. **É melhor que uma estratégia ingênua?** Baselines avaliadas nas mesmas
   horas: "sempre sobe" (classe majoritária) e "momentum" (repete a direção do
   último candle já fechado).
3. **Existe relação linear?** Pearson entre o Sentiment Score e o retorno, com
   p-valor por teste de permutação.

Tudo em Python puro (``math`` e ``random``) para não acrescentar scipy às
dependências só por três fórmulas.
"""

import math
import random

# Mínimo de pares para calcular qualquer Pearson.
MIN_PARES_PEARSON = 3

# Nível de significância usado em todo o projeto.
ALFA = 0.05

# Permutações do teste de Pearson. Semente fixa: o mesmo banco sempre gera o
# mesmo p-valor, o que importa para o relatório ser reprodutível.
PERMUTACOES = 1000
SEMENTE = 42


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


def p_valor_pearson(xs: list[float], ys: list[float]) -> float | None:
    """P-valor bilateral de Pearson por permutação.

    Embaralha ``ys`` e conta quantas vezes a correlação ao acaso fica tão
    extrema quanto a observada. Não supõe normalidade dos retornos, que em
    cripto têm caudas pesadas.
    """
    if pearson(xs, ys) is None:
        return None

    # Embaralhar não muda médias nem variâncias: basta comparar o produto
    # escalar das séries centradas, sem recalcular o Pearson inteiro.
    media_x = sum(xs) / len(xs)
    media_y = sum(ys) / len(ys)
    xc = [x - media_x for x in xs]
    yc = [y - media_y for y in ys]
    observado = abs(sum(a * b for a, b in zip(xc, yc))) - 1e-12

    rng = random.Random(SEMENTE)
    extremos = 0
    for _ in range(PERMUTACOES):
        rng.shuffle(yc)
        if abs(sum(a * b for a, b in zip(xc, yc))) >= observado:
            extremos += 1
    # +1 no numerador e no denominador: o p-valor nunca é exatamente zero.
    return round((extremos + 1) / (PERMUTACOES + 1), 4)


def p_valor_binomial(acertos: int, n: int, p: float = 0.5) -> float | None:
    """P-valor bilateral exato do teste binomial (H0: taxa de acerto = ``p``).

    Soma a probabilidade de todos os resultados tão ou menos prováveis que o
    observado — a mesma definição de ``scipy.stats.binomtest``.
    """
    if n <= 0:
        return None
    probs = [math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]
    observada = probs[acertos]
    total = sum(pr for pr in probs if pr <= observada * (1 + 1e-7))
    return round(min(1.0, total), 4)


def intervalo_wilson(acertos: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Intervalo de confiança de Wilson (95%) para uma proporção, em %.

    Melhor que o intervalo "p ± 1,96·erro" em amostras pequenas, que pode até
    sair de [0, 100].
    """
    if n <= 0:
        return None
    phat = acertos / n
    denom = 1 + z**2 / n
    centro = (phat + z**2 / (2 * n)) / denom
    margem = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denom
    # Com 0 ou n acertos a conta dá -0.0 / 100.0000001 por arredondamento.
    return (round(max(0.0, centro - margem) * 100, 1),
            round(min(1.0, centro + margem) * 100, 1))


def _sinal(valor: float, limiar: float) -> int:
    if valor > limiar:
        return 1
    if valor < -limiar:
        return -1
    return 0


def avaliar_horizonte(
    observacoes: list[dict],
    limiar_retorno_pct: float,
) -> dict:
    """Avalia um horizonte de previsão.

    Cada observação tem ``score`` (Sentiment Score da hora), ``direcao``
    (+1, -1 ou 0 — já com o limiar de sentimento aplicado), ``retorno`` (% no
    horizonte) e ``momentum`` (% do último candle fechado antes do horizonte,
    ou ``None``).

    Só entram na taxa de acerto as horas com sentimento não neutro e retorno
    acima do limiar — "neutro" e "estável" não são previsões. As baselines são
    medidas exatamente nessas mesmas horas, senão a comparação não é justa.
    """
    comparaveis = [
        o for o in observacoes
        if o["direcao"] != 0 and _sinal(o["retorno"], limiar_retorno_pct) != 0
    ]
    n = len(comparaveis)
    acertos = sum(
        1 for o in comparaveis if o["direcao"] == _sinal(o["retorno"], limiar_retorno_pct)
    )
    subiu = sum(1 for o in comparaveis if o["retorno"] > 0)

    com_momentum = [
        o for o in comparaveis
        if o["momentum"] is not None and _sinal(o["momentum"], limiar_retorno_pct) != 0
    ]
    acertos_momentum = sum(
        1 for o in com_momentum
        if _sinal(o["momentum"], limiar_retorno_pct)
        == _sinal(o["retorno"], limiar_retorno_pct)
    )

    scores = [o["score"] for o in observacoes]
    retornos = [o["retorno"] for o in observacoes]
    p_valor = p_valor_binomial(acertos, n)

    return {
        "horas_com_retorno": len(observacoes),
        "total_comparavel": n,
        "acertos": acertos,
        "taxa_acerto_pct": round(acertos / n * 100, 1) if n else None,
        "ic95_pct": intervalo_wilson(acertos, n),
        "p_valor": p_valor,
        "significativo": p_valor is not None and p_valor < ALFA,
        "baseline_sempre_sobe_pct": round(subiu / n * 100, 1) if n else None,
        "baseline_momentum_pct": (
            round(acertos_momentum / len(com_momentum) * 100, 1)
            if com_momentum else None
        ),
        "baseline_momentum_n": len(com_momentum),
        "pearson": pearson(scores, retornos),
        "pearson_p_valor": p_valor_pearson(scores, retornos),
    }


def conclusao(horizontes: list[dict]) -> str:
    """Resume em uma frase o que os testes permitem afirmar."""
    avaliados = [h for h in horizontes if h["total_comparavel"] > 0]
    if not avaliados:
        return "Sem horas comparáveis suficientes para qualquer teste."

    acima = [
        h for h in avaliados
        if h["significativo"] and h["taxa_acerto_pct"] > 50
        and h["taxa_acerto_pct"] > (h["baseline_sempre_sobe_pct"] or 0)
    ]
    if acima:
        rotulos = ", ".join(h["rotulo"] for h in acima)
        return (
            f"O sentimento acertou a direção do preço acima do acaso (p < {ALFA}) "
            f"e acima da baseline \"sempre sobe\" em: {rotulos}."
        )

    abaixo = [h for h in avaliados if h["significativo"] and h["taxa_acerto_pct"] < 50]
    if abaixo:
        rotulos = ", ".join(h["rotulo"] for h in abaixo)
        return (
            f"O sentimento acertou significativamente MENOS que o acaso em: {rotulos} "
            "— um sinal contrário (o mercado tende a ir contra o humor das redes)."
        )

    return (
        "Em nenhum horizonte a taxa de acerto difere do acaso com significância "
        f"de {int(ALFA * 100)}%: com esta amostra, não há evidência de que o "
        "sentimento antecipe a direção do preço."
    )
