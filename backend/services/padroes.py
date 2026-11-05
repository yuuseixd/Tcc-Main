"""Leitura de padrões gráficos clássicos (price action).

Detecção determinística, em Python puro — as mesmas velas dão sempre o mesmo
resultado, e cada regra abaixo pode ser explicada e testada.

Padrões
-------
Reversão de baixa para alta: OCOI, fundo duplo, fundo triplo.
Reversão de alta para baixa: OCO, topo duplo, topo triplo.
Continuação: retângulo de alta/baixa, triângulo ascendente/descendente e
flâmula de alta/baixa.

Etapas comuns
-------------
1. **Zigzag**: marca os topos e fundos relevantes, ignorando oscilações
   menores que ``limiar`` (proporcional à volatilidade típica da série). Roda
   em três escalas, para enxergar padrões pequenos e grandes.
2. **Geometria**: cada padrão é uma sequência de pivôs com regras próprias
   (ver os detectores abaixo) e uma **linha de rompimento**.
3. **Rompimento**: o padrão só é *confirmado* quando um candle fecha além da
   linha de rompimento, na direção do padrão. Fechar além do nível de
   invalidação antes disso o *invalida*.
4. **Projeção (movimento medido)**: alvo = linha no rompimento ± altura do
   padrão; stop = nível de invalidação.

Os padrões de baixa usam o mesmo código dos de alta com o sinal invertido
(``s = -1``): "acertar" é o preço cair até o alvo antes de subir até o stop.

Confiabilidade
--------------
Em vez de assumir a fama dos padrões, :func:`resumir` mede no histórico
quantos padrões confirmados atingiram o alvo antes do stop e compara com uma
entrada **qualquer** (sem padrão) com o mesmo alvo e stop em porcentagem.
"""

from dataclasses import dataclass
from statistics import median

from services import estatistica

ALTA, BAIXA = 1, -1

TIPOS = {
    "ocoi": ("OCOI (ombro-cabeça-ombro invertido)", ALTA, "reversão",
             "Três fundos, o do meio mais profundo. Rompe para cima a linha de pescoço."),
    "oco": ("OCO (ombro-cabeça-ombro)", BAIXA, "reversão",
            "Três topos, o do meio mais alto. Rompe para baixo a linha de pescoço."),
    "fundo_duplo": ("Fundo duplo", ALTA, "reversão",
                    "Dois fundos no mesmo nível após queda. Rompe para cima o topo entre eles."),
    "fundo_triplo": ("Fundo triplo", ALTA, "reversão",
                     "Três fundos no mesmo nível após queda. Rompe para cima a resistência."),
    "topo_duplo": ("Topo duplo", BAIXA, "reversão",
                   "Dois topos no mesmo nível após alta. Rompe para baixo o fundo entre eles."),
    "topo_triplo": ("Topo triplo", BAIXA, "reversão",
                    "Três topos no mesmo nível após alta. Rompe para baixo o suporte."),
    "retangulo_alta": ("Retângulo de alta", ALTA, "continuação",
                       "Lateralização entre suporte e resistência após alta. Rompe para cima."),
    "retangulo_baixa": ("Retângulo de baixa", BAIXA, "continuação",
                        "Lateralização entre suporte e resistência após queda. Rompe para baixo."),
    "triangulo_ascendente": ("Triângulo ascendente", ALTA, "continuação",
                             "Topos no mesmo nível e fundos subindo. Rompe para cima."),
    "triangulo_descendente": ("Triângulo descendente", BAIXA, "continuação",
                              "Fundos no mesmo nível e topos caindo. Rompe para baixo."),
    "flamula_alta": ("Flâmula de alta", ALTA, "continuação",
                     "Subida forte (mastro) e consolidação convergente curta. Rompe para cima."),
    "flamula_baixa": ("Flâmula de baixa", BAIXA, "continuação",
                      "Queda forte (mastro) e consolidação convergente curta. Rompe para baixo."),
}

# ── Parâmetros (fixados a priori, não ajustados ao resultado) ───────────────
# Zigzag: oscilação mínima = FATOR x amplitude mediana dos candles. Padrões
# existem em várias escalas; cada fator enxerga uma, e o mesmo padrão visto
# em duas escalas conta uma vez só, na escala maior.
FATORES_ZIGZAG = (2.0, 3.0, 4.0)
LIMIAR_MINIMO = 0.01            # nunca abaixo de 1%
# Pivôs-chave a até N candles um do outro são o mesmo padrão.
TOLERANCIA_MESMO_PADRAO = 2
# OCO/OCOI: cabeça pelo menos 15% mais funda que o ombro mais fundo.
CABECA_DESTAQUE = 1.15
# OCO/OCOI: ombro menor com pelo menos metade da profundidade do maior.
SIMETRIA_OMBROS = 0.5
# OCO/OCOI: duração de um lado no máximo 3x a do outro.
SIMETRIA_TEMPO = 3.0
# "Mesmo nível": diferença de até 20% da altura do padrão (topos/fundos
# duplos e triplos, lados planos de retângulos e triângulos).
TOL_NIVEL = 0.20
# Triângulos: o lado inclinado avança pelo menos 30% da altura.
INCLINACAO_TRIANGULO = 0.30
# Flâmulas: consolidação recua no máximo 50% do mastro e dura no máximo 4x ele.
RETRACAO_FLAMULA = 0.50
DURACAO_FLAMULA = 4.0
# Rompimento tem de vir em até 1x a largura do padrão depois do último pivô.
JANELA_ROMPIMENTO = 1.0
# Desfecho (alvo ou stop) observado em até 2x a largura do padrão.
JANELA_DESFECHO = 2.0
# Linha de base: entradas igualmente espaçadas na série (custo fixo).
AMOSTRAS_BASELINE = 300

FUNDO = "fundo"
TOPO = "topo"


@dataclass(frozen=True)
class Pivo:
    i: int          # índice do candle
    preco: float
    tipo: str       # FUNDO / TOPO


@dataclass
class Formacao:
    """Geometria de um padrão encontrado, antes do acompanhamento."""

    tipo: str
    pivos: list            # [(nome, rótulo, Pivo)]
    linhas: list           # [(nome, Pivo|tuple, Pivo|tuple)] — pontos (i, preço)
    rompimento: tuple      # ((i1, p1), (i2, p2)) — reta de confirmação
    altura: float          # movimento medido
    invalidacao: float
    i_primeiro: int
    i_ultimo: int
    i_chave: int           # identifica o padrão entre escalas


def _alta(c) -> float:
    return c.maxima if c.maxima is not None else max(c.abertura, c.fechamento)


def _baixa(c) -> float:
    return c.minima if c.minima is not None else min(c.abertura, c.fechamento)


def limiares_automaticos(candles) -> list[float]:
    """Oscilações mínimas do zigzag (uma por escala), pela volatilidade da série."""
    amplitudes = [
        (_alta(c) - _baixa(c)) / c.fechamento for c in candles if c.fechamento
    ]
    base = median(amplitudes) if amplitudes else 0.0
    return sorted({max(LIMIAR_MINIMO, f * base) for f in FATORES_ZIGZAG})


def zigzag(candles, limiar: float) -> list[Pivo]:
    """Topos e fundos confirmados por uma reversão de pelo menos ``limiar``.

    O último extremo da série ainda não foi confirmado (o preço não voltou o
    suficiente) e por isso não entra — um padrão só usa pivôs definitivos.
    """
    if len(candles) < 2:
        return []

    pivos: list[Pivo] = []
    alto, i_alto = _alta(candles[0]), 0
    baixo, i_baixo = _baixa(candles[0]), 0
    direcao = None  # TOPO = procurando topo (subindo); FUNDO = procurando fundo

    for i in range(1, len(candles)):
        h, l = _alta(candles[i]), _baixa(candles[i])

        if direcao is None:
            if h > alto:
                alto, i_alto = h, i
            if l < baixo:
                baixo, i_baixo = l, i
            if i_baixo < i_alto and alto >= baixo * (1 + limiar):
                pivos.append(Pivo(i_baixo, baixo, FUNDO))
                direcao = TOPO
            elif i_alto < i_baixo and baixo <= alto * (1 - limiar):
                pivos.append(Pivo(i_alto, alto, TOPO))
                direcao = FUNDO
            continue

        if direcao == TOPO:
            if h >= alto:
                alto, i_alto = h, i
            elif l <= alto * (1 - limiar):
                pivos.append(Pivo(i_alto, alto, TOPO))
                direcao, baixo, i_baixo = FUNDO, l, i
        else:
            if l <= baixo:
                baixo, i_baixo = l, i
            elif h >= baixo * (1 + limiar):
                pivos.append(Pivo(i_baixo, baixo, FUNDO))
                direcao, alto, i_alto = TOPO, h, i

    return pivos


def _reta(a, b):
    """Função i -> preço da reta pelos pontos ``a`` e ``b`` (Pivo ou (i, p))."""
    (i1, p1), (i2, p2) = _ponto(a), _ponto(b)
    if i1 == i2:
        return lambda i: p1
    inclinacao = (p2 - p1) / (i2 - i1)
    return lambda i: p1 + inclinacao * (i - i1)


def _ponto(x) -> tuple:
    return (x.i, x.preco) if isinstance(x, Pivo) else x


def _extremo(s: int) -> str:
    """Tipo de pivô que forma os "fundos" do padrão na direção ``s``."""
    return FUNDO if s == ALTA else TOPO


def _mesmo_nivel(precos: list[float], altura: float) -> bool:
    return altura > 0 and max(precos) - min(precos) <= TOL_NIVEL * altura


# ═══════════════════════════════════════════════════════════════════════════
#  Detectores — cada um devolve as Formacoes de uma direção num zigzag
# ═══════════════════════════════════════════════════════════════════════════


def _cabeca_ombros(candles, pivos: list[Pivo], s: int) -> list[Formacao]:
    """OCOI (s=+1) e OCO (s=-1)."""
    tipo = "ocoi" if s == ALTA else "oco"
    achados = []
    for k in range(1, len(pivos) - 4):
        oe, t1, cab, t2, od = pivos[k:k + 5]
        if oe.tipo != _extremo(s):
            continue
        pescoco = _reta(t1, t2)

        def prof(p):  # distância do pivô até o pescoço, contra a direção
            return s * (pescoco(p.i) - p.preco)

        p_cab, p_oe, p_od = prof(cab), prof(oe), prof(od)
        if min(p_cab, p_oe, p_od) <= 0:
            continue
        if p_cab < CABECA_DESTAQUE * max(p_oe, p_od):
            continue
        if min(p_oe, p_od) / max(p_oe, p_od) < SIMETRIA_OMBROS:
            continue
        esq, dir_ = cab.i - oe.i, od.i - cab.i
        if max(esq, dir_) > SIMETRIA_TEMPO * min(esq, dir_):
            continue
        # Reversão: o pivô anterior fica além do pescoço (havia tendência).
        antes = pivos[k - 1]
        if s * (antes.preco - pescoco(antes.i)) <= 0:
            continue

        achados.append(Formacao(
            tipo=tipo,
            pivos=[("ombro_esquerdo", "OE", oe), ("topo_esquerdo", "", t1),
                   ("cabeca", "Cabeça", cab), ("topo_direito", "", t2),
                   ("ombro_direito", "OD", od)],
            linhas=[("Linha de pescoço", t1, t2)],
            rompimento=(_ponto(t1), _ponto(t2)),
            altura=p_cab, invalidacao=od.preco,
            i_primeiro=oe.i, i_ultimo=od.i, i_chave=cab.i,
        ))
    return achados


def _multiplos(candles, pivos: list[Pivo], s: int, toques: int) -> list[Formacao]:
    """Fundo duplo/triplo (s=+1) e topo duplo/triplo (s=-1)."""
    nome = {(ALTA, 2): "fundo_duplo", (ALTA, 3): "fundo_triplo",
            (BAIXA, 2): "topo_duplo", (BAIXA, 3): "topo_triplo"}[(s, toques)]
    rotulo = "F" if s == ALTA else "T"
    n = 2 * toques - 1
    achados = []
    for k in range(1, len(pivos) - n + 1):
        janela = pivos[k:k + n]
        if janela[0].tipo != _extremo(s):
            continue
        extremos, reacoes = janela[0::2], janela[1::2]
        # Linha de rompimento horizontal na reação mais distante.
        nivel = max((r.preco for r in reacoes), key=lambda p: s * p)
        mais_fundo = min((e.preco for e in extremos), key=lambda p: s * p)
        altura = s * (nivel - mais_fundo)
        if not _mesmo_nivel([e.preco for e in extremos], altura):
            continue
        antes = pivos[k - 1]
        if s * (antes.preco - nivel) <= 0:
            continue

        achados.append(Formacao(
            tipo=nome,
            pivos=[(f"{rotulo.lower()}{j // 2 + 1}" if j % 2 == 0 else f"reacao{j // 2 + 1}",
                    f"{rotulo}{j // 2 + 1}" if j % 2 == 0 else "", p)
                   for j, p in enumerate(janela)],
            linhas=[("Resistência" if s == ALTA else "Suporte",
                     (janela[0].i, nivel), (janela[-1].i, nivel))],
            rompimento=((janela[0].i, nivel), (janela[-1].i, nivel)),
            altura=altura, invalidacao=mais_fundo,
            i_primeiro=janela[0].i, i_ultimo=janela[-1].i, i_chave=janela[0].i,
        ))
    return achados


def _tendencia_antes(candles, i_inicio: int, largura: int) -> float | None:
    """Fechamento ``largura`` candles antes do padrão (None se não houver)."""
    j = i_inicio - max(1, largura)
    return candles[j].fechamento if j >= 0 else None


def _faixa_lateral(pivos: list[Pivo], k: int):
    """Maior sequência a partir de ``k`` com topos e fundos em níveis estáveis."""
    fim = k + 4
    if fim > len(pivos):
        return None
    while True:
        janela = pivos[k:fim]
        topos = [p.preco for p in janela if p.tipo == TOPO]
        fundos = [p.preco for p in janela if p.tipo == FUNDO]
        altura = sum(topos) / len(topos) - sum(fundos) / len(fundos)
        ok = _mesmo_nivel(topos, altura) and _mesmo_nivel(fundos, altura)
        if not ok:
            return pivos[k:fim - 1] if fim - 1 - k >= 4 else None
        if fim == len(pivos):
            return janela
        fim += 1


def _retangulos(candles, pivos: list[Pivo], s: int) -> list[Formacao]:
    """Retângulo de alta (s=+1, após alta) e de baixa (s=-1, após queda)."""
    achados = []
    k = 0
    while k <= len(pivos) - 4:
        janela = _faixa_lateral(pivos, k)
        if not janela:
            k += 1
            continue
        resistencia = max(p.preco for p in janela if p.tipo == TOPO)
        suporte = min(p.preco for p in janela if p.tipo == FUNDO)
        largura = janela[-1].i - janela[0].i
        antes = _tendencia_antes(candles, janela[0].i, largura)
        veio_certo = antes is not None and (
            antes < suporte if s == ALTA else antes > resistencia)
        if veio_certo:
            gatilho = resistencia if s == ALTA else suporte
            achados.append(Formacao(
                tipo="retangulo_alta" if s == ALTA else "retangulo_baixa",
                pivos=[(f"toque{j + 1}", "", p) for j, p in enumerate(janela)],
                linhas=[("Resistência", (janela[0].i, resistencia), (janela[-1].i, resistencia)),
                        ("Suporte", (janela[0].i, suporte), (janela[-1].i, suporte))],
                rompimento=((janela[0].i, gatilho), (janela[-1].i, gatilho)),
                altura=resistencia - suporte,
                invalidacao=suporte if s == ALTA else resistencia,
                i_primeiro=janela[0].i, i_ultimo=janela[-1].i, i_chave=janela[0].i,
            ))
        # Os pivôs de um retângulo não iniciam outro.
        k += len(janela) - 1
    return achados


def _triangulos(candles, pivos: list[Pivo], s: int) -> list[Formacao]:
    """Ascendente (s=+1: topos planos, fundos subindo) e descendente (s=-1)."""
    plano, inclinado = (TOPO, FUNDO) if s == ALTA else (FUNDO, TOPO)
    achados = []
    k = 0
    while k <= len(pivos) - 4:
        melhor = None
        for fim in range(k + 4, len(pivos) + 1):
            janela = pivos[k:fim]
            lado_plano = [p for p in janela if p.tipo == plano]
            lado_incl = [p for p in janela if p.tipo == inclinado]
            nivel = max((p.preco for p in lado_plano), key=lambda x: s * x)
            altura = s * (nivel - lado_incl[0].preco)
            subindo = all(s * (b.preco - a.preco) > 0
                          for a, b in zip(lado_incl, lado_incl[1:]))
            avanco = s * (lado_incl[-1].preco - lado_incl[0].preco)
            if not (_mesmo_nivel([p.preco for p in lado_plano], altura) and subindo
                    and avanco >= INCLINACAO_TRIANGULO * altura
                    and s * (nivel - lado_incl[-1].preco) > 0):
                break
            melhor = (janela, nivel, altura, lado_incl)
        if not melhor:
            k += 1
            continue
        janela, nivel, altura, lado_incl = melhor
        achados.append(Formacao(
            tipo="triangulo_ascendente" if s == ALTA else "triangulo_descendente",
            pivos=[(f"toque{j + 1}", "", p) for j, p in enumerate(janela)],
            linhas=[("Resistência" if s == ALTA else "Suporte",
                     (janela[0].i, nivel), (janela[-1].i, nivel)),
                    ("Fundos subindo" if s == ALTA else "Topos caindo",
                     lado_incl[0], lado_incl[-1])],
            rompimento=((janela[0].i, nivel), (janela[-1].i, nivel)),
            altura=altura, invalidacao=lado_incl[-1].preco,
            i_primeiro=janela[0].i, i_ultimo=janela[-1].i, i_chave=janela[0].i,
        ))
        k += len(janela) - 1
    return achados


def _flamulas(candles, pivos: list[Pivo], s: int) -> list[Formacao]:
    """Flâmula de alta (s=+1) e de baixa (s=-1): mastro + consolidação convergente."""
    achados = []
    for k in range(len(pivos) - 4):
        a, b, c1, d1, c2 = pivos[k:k + 5]
        if a.tipo != _extremo(s):
            continue
        mastro = s * (b.preco - a.preco)
        if mastro <= 0:
            continue
        # Consolidação: recua pouco, converge (topos caem e fundos sobem, ou o
        # inverso na de baixa) e é curta perto do mastro.
        if s * (b.preco - c1.preco) > RETRACAO_FLAMULA * mastro:
            continue
        if not (s * (d1.preco - b.preco) < 0 and s * (c2.preco - c1.preco) > 0):
            continue
        if c2.i - b.i > DURACAO_FLAMULA * (b.i - a.i):
            continue
        achados.append(Formacao(
            tipo="flamula_alta" if s == ALTA else "flamula_baixa",
            pivos=[("mastro_inicio", "Mastro", a), ("mastro_fim", "", b),
                   ("c1", "", c1), ("d1", "", d1), ("c2", "", c2)],
            linhas=[("Mastro", a, b), ("Lado de rompimento", b, d1), ("Lado oposto", c1, c2)],
            rompimento=(_ponto(b), _ponto(d1)),
            altura=mastro, invalidacao=c2.preco,
            i_primeiro=b.i, i_ultimo=c2.i, i_chave=b.i,
        ))
    return achados


def _detectores():
    return [
        lambda c, p: _cabeca_ombros(c, p, ALTA),
        lambda c, p: _cabeca_ombros(c, p, BAIXA),
        lambda c, p: _multiplos(c, p, ALTA, 2),
        lambda c, p: _multiplos(c, p, ALTA, 3),
        lambda c, p: _multiplos(c, p, BAIXA, 2),
        lambda c, p: _multiplos(c, p, BAIXA, 3),
        lambda c, p: _retangulos(c, p, ALTA),
        lambda c, p: _retangulos(c, p, BAIXA),
        lambda c, p: _triangulos(c, p, ALTA),
        lambda c, p: _triangulos(c, p, BAIXA),
        lambda c, p: _flamulas(c, p, ALTA),
        lambda c, p: _flamulas(c, p, BAIXA),
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  Acompanhamento: rompimento, alvo, stop e desfecho
# ═══════════════════════════════════════════════════════════════════════════


def _desfecho(candles, inicio: int, alvo: float, stop: float, horizonte: int,
              s: int = ALTA):
    """O que veio primeiro depois de ``inicio``: alvo, stop ou nenhum dos dois.

    Se alvo e stop caem no mesmo candle não dá para saber a ordem; conta-se
    stop (leitura conservadora).
    """
    n = len(candles)
    fim = inicio + horizonte
    for j in range(inicio + 1, min(n, fim + 1)):
        contra = _baixa(candles[j]) if s == ALTA else _alta(candles[j])
        a_favor = _alta(candles[j]) if s == ALTA else _baixa(candles[j])
        if s * (contra - stop) <= 0:
            return "stop", j
        if s * (a_favor - alvo) >= 0:
            return "alvo", j
    if fim >= n:
        return "em_andamento", n - 1
    return "sem_desfecho", fim


def _acompanhar(candles, f: Formacao, limiar: float) -> dict:
    """Converte a formação em dict com status, alvo, stop e desfecho."""
    s = TIPOS[f.tipo][1]
    n = len(candles)
    linha = _reta(*f.rompimento)

    def ts(i):
        return candles[i].timestamp.isoformat() + "Z"

    padrao = {
        "tipo": f.tipo,
        "direcao": "alta" if s == ALTA else "baixa",
        "pivos": [{"nome": nome, "rotulo": rot, "tipo": p.tipo, "i": p.i,
                   "preco": p.preco, "timestamp": ts(p.i)} for nome, rot, p in f.pivos],
        "linhas": [{"nome": nome, **dict(zip(("i1", "p1"), _ponto(a))),
                    **dict(zip(("i2", "p2"), _ponto(b)))} for nome, a, b in f.linhas],
        "rompimento": {"i1": f.rompimento[0][0], "p1": f.rompimento[0][1],
                       "i2": f.rompimento[1][0], "p2": f.rompimento[1][1]},
        "altura": f.altura,
        "invalidacao": f.invalidacao,
        "i_ultimo_pivo": f.i_ultimo,
        "timestamp_formacao": ts(f.i_ultimo),
        "escala_pct": limiar * 100,
    }

    largura = max(1, f.i_ultimo - f.i_primeiro)
    limite = f.i_ultimo + max(1, round(JANELA_ROMPIMENTO * largura))
    rompeu = None
    for j in range(f.i_ultimo + 1, min(n, limite + 1)):
        fech = candles[j].fechamento
        if s * (fech - linha(j)) > 0:
            rompeu = j
            break
        if s * (fech - f.invalidacao) < 0:
            padrao.update(status="invalidado", i_status=j)
            break

    if rompeu is None and "status" not in padrao:
        if limite >= n - 1:
            gatilho = linha(n - 1)
            padrao.update(status="em_formacao", i_status=n - 1, gatilho=gatilho,
                          alvo=gatilho + s * f.altura)
        else:
            padrao.update(status="expirado", i_status=limite)

    if rompeu is not None:
        entrada = candles[rompeu].fechamento
        alvo = linha(rompeu) + s * f.altura
        horizonte = max(1, round(JANELA_DESFECHO * largura))
        resultado, i_fim = _desfecho(candles, rompeu, alvo, f.invalidacao, horizonte, s)
        padrao.update(
            status="confirmado", i_status=rompeu, i_rompimento=rompeu,
            entrada=entrada, gatilho=linha(rompeu), alvo=alvo,
            alvo_pct=s * (alvo / entrada - 1) * 100,
            stop_pct=s * (1 - f.invalidacao / entrada) * 100,
            horizonte=horizonte, desfecho=resultado, i_desfecho=i_fim,
            candles_ate_desfecho=i_fim - rompeu,
            # Retorno da operação na direção do padrão (positivo = a favor).
            retorno_pct=s * (candles[i_fim].fechamento / entrada - 1) * 100,
        )

    padrao["timestamp_status"] = ts(padrao["i_status"])
    return padrao


def _sem_redundancia(formacoes: list[Formacao]) -> list[Formacao]:
    """Tira duplos contidos em triplos do mesmo lado (o triplo é o padrão)."""
    triplos = {(f.tipo.split("_")[0], f.i_primeiro, f.i_ultimo)
               for f in formacoes if f.tipo.endswith("_triplo")}
    resultado = []
    for f in formacoes:
        if f.tipo.endswith("_duplo"):
            lado = f.tipo.split("_")[0]
            if any(lado == t and ini <= f.i_primeiro and f.i_ultimo <= fim
                   for t, ini, fim in triplos):
                continue
        resultado.append(f)
    return resultado


def detectar(candles, limiares: list[float] | None = None,
             tipos: set[str] | None = None) -> list[dict]:
    """Todos os padrões da série, do mais antigo ao mais recente.

    Roda o zigzag em cada escala, da maior para a menor; um padrão do mesmo
    tipo com o mesmo pivô-chave de outro de escala maior é descartado.
    """
    if limiares is None:
        limiares = limiares_automaticos(candles)
    vistos: dict[str, list[int]] = {}
    padroes: list[dict] = []
    for limiar in sorted(limiares, reverse=True):
        pivos = zigzag(candles, limiar)
        formacoes = [f for detector in _detectores() for f in detector(candles, pivos)]
        for f in _sem_redundancia(formacoes):
            if tipos and f.tipo not in tipos:
                continue
            chaves = vistos.setdefault(f.tipo, [])
            if any(abs(f.i_chave - c) <= TOLERANCIA_MESMO_PADRAO for c in chaves):
                continue
            chaves.append(f.i_chave)
            padroes.append(_acompanhar(candles, f, limiar))
    padroes.sort(key=lambda p: (p["i_ultimo_pivo"], p["tipo"]))
    return padroes


def detectar_ocoi(candles, limiares: list[float] | None = None) -> list[dict]:
    """Só os OCOIs (atalho usado pelos testes e pelo backtest)."""
    return detectar(candles, limiares, {"ocoi"})


# ═══════════════════════════════════════════════════════════════════════════
#  Avaliação histórica
# ═══════════════════════════════════════════════════════════════════════════


def _taxa_baseline(candles, alvo_pct: float, stop_pct: float, horizonte: int,
                   s: int = ALTA) -> float | None:
    """Fração de entradas quaisquer que bateriam o mesmo alvo antes do stop.

    Usa ``AMOSTRAS_BASELINE`` entradas igualmente espaçadas na série.
    """
    ultimo = len(candles) - horizonte - 1
    if ultimo <= 0:
        return None
    passo = max(1, ultimo // AMOSTRAS_BASELINE)
    acertos = total = 0
    for i in range(0, ultimo, passo):
        entrada = candles[i].fechamento
        resultado, _ = _desfecho(
            candles, i, entrada * (1 + s * alvo_pct / 100),
            entrada * (1 - s * stop_pct / 100), horizonte, s,
        )
        total += 1
        acertos += resultado == "alvo"
    return acertos / total if total else None


def _resolvido(p: dict) -> bool:
    return p["status"] == "confirmado" and p["desfecho"] != "em_andamento"


def anotar_baseline(candles, padroes: list[dict]) -> None:
    """Grava em cada padrão resolvido a taxa da linha de base na sua série."""
    for p in padroes:
        if _resolvido(p) and "baseline" not in p:
            p["baseline"] = _taxa_baseline(
                candles, p["alvo_pct"], p["stop_pct"], p["horizonte"],
                ALTA if p.get("direcao", "alta") == "alta" else BAIXA,
            )


def avaliar_historico(candles, padroes: list[dict]) -> dict:
    """Avaliação dos padrões de uma série (ver :func:`resumir`)."""
    anotar_baseline(candles, padroes)
    return resumir(padroes)


def resumir(padroes: list[dict]) -> dict:
    """Taxa de acerto dos padrões confirmados e comparação com a linha de base.

    Acerto = atingir o alvo antes do stop, dentro do horizonte. Padrões ainda
    em andamento ficam de fora; "sem desfecho" conta como erro. Os padrões
    precisam ter passado por :func:`anotar_baseline` — o que permite juntar
    padrões de moedas diferentes, cada um com a linha de base da sua série.
    """
    resolvidos = [p for p in padroes if _resolvido(p)]
    acertos = sum(p["desfecho"] == "alvo" for p in resolvidos)
    n = len(resolvidos)

    bases = [p["baseline"] for p in resolvidos if p.get("baseline") is not None]
    baseline = sum(bases) / len(bases) if bases else None

    contagem = {s: sum(p["status"] == s for p in padroes)
                for s in ("confirmado", "invalidado", "expirado", "em_formacao")}
    taxa = acertos / n * 100 if n else None
    p_valor = (estatistica.p_valor_binomial(acertos, n, baseline)
               if n and baseline is not None else None)

    no_alvo = [p for p in resolvidos if p["desfecho"] == "alvo"]
    return {
        "padroes_encontrados": len(padroes),
        **{f"total_{s}": v for s, v in contagem.items()},
        "avaliados": n,
        "acertos": acertos,
        "stops": sum(p["desfecho"] == "stop" for p in resolvidos),
        "sem_desfecho": sum(p["desfecho"] == "sem_desfecho" for p in resolvidos),
        "taxa_acerto_pct": taxa,
        "ic95_pct": estatistica.intervalo_wilson(acertos, n) if n else None,
        "baseline_pct": baseline * 100 if baseline is not None else None,
        # Pontos percentuais acima (ou abaixo) de uma entrada qualquer.
        "vantagem_pp": (taxa - baseline * 100
                        if taxa is not None and baseline is not None else None),
        "p_valor": p_valor,
        "significativo": bool(
            p_valor is not None and p_valor < estatistica.ALFA
            and taxa is not None and taxa > baseline * 100
        ),
        "amostra_suficiente": n >= 30,
        "candles_medios_ate_alvo": (
            sum(p["candles_ate_desfecho"] for p in no_alvo) / len(no_alvo)
            if no_alvo else None
        ),
        "alvo_medio_pct": (sum(p["alvo_pct"] for p in resolvidos) / n) if n else None,
        "stop_medio_pct": (sum(p["stop_pct"] for p in resolvidos) / n) if n else None,
    }


def ativo(p: dict) -> bool:
    """Padrão que interessa agora: em formação ou confirmado e em andamento."""
    return p["status"] == "em_formacao" or p.get("desfecho") == "em_andamento"


def padrao_atual(padroes: list[dict]) -> dict | None:
    """O padrão ativo mais recente da lista."""
    for p in reversed(padroes):
        if ativo(p):
            return p
    return None


def analisar(candles) -> dict:
    """Todos os padrões da série e quais estão ativos agora."""
    limiares = limiares_automaticos(candles)
    padroes = detectar(candles, limiares)
    return {
        "escalas_zigzag_pct": [lim * 100 for lim in limiares],
        "padroes": padroes,
        "ativos": [k for k, p in enumerate(padroes) if ativo(p)],
    }


def catalogo() -> list[dict]:
    """Nome, direção e descrição de cada tipo, na ordem de exibição."""
    return [
        {"tipo": tipo, "nome": nome, "direcao": "alta" if s == ALTA else "baixa",
         "categoria": categoria, "descricao": descricao}
        for tipo, (nome, s, categoria, descricao) in TIPOS.items()
    ]
