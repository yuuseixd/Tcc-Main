"""Perfil de investimento: questionário, pontuação e classificação.

Fundamentação
-------------
A **Resolução CVM 30/2021** (art. 3º) manda verificar, no mínimo, três eixos, e
lista os itens de cada um. As 11 perguntas abaixo cobrem todos esses itens, e
cada uma indica o artigo que a motiva (campo ``base``):

* Objetivos (§ 1º): período do investimento, preferências declaradas quanto a
  riscos e finalidades.
* Situação financeira (§ 2º): receitas regulares, patrimônio e ativos que o
  compõem, necessidade futura de recursos.
* Conhecimento (§ 3º): produtos com que tem familiaridade, natureza, volume e
  frequência das operações já feitas, formação e experiência profissional.

O art. 4º manda classificar o cliente em "categorias de perfil de risco
previamente estabelecidas", sem dizer quais. Usamos as três que a ANBIMA cita
(conservador, moderado e arrojado). A ANBIMA, desde 05/09/2023, também passou a
considerar o **baixo conhecimento** ao enquadrar o perfil conservador; daí a
trava de conhecimento abaixo.

O que é escolha do projeto (a norma não define): a pontuação de 1 a 4 por
resposta, os cortes em terços da faixa de pontos e as travas. Por isso este
questionário é um instrumento **educacional**, não uma recomendação de
investimento nem substitui a avaliação formal de uma instituição financeira.
Criptomoedas, além disso, nem sempre são "valores mobiliários" no sentido da
Resolução 30.

Pontuação: cada resposta vale de 1 (mais cauteloso) a 4 (mais arrojado). A soma
(de 11 a 44) é dividida em terços: até 22 Conservador, até 33 Moderado, acima
disso Arrojado.
"""

import json

PERFIS = ("Conservador", "Moderado", "Arrojado")

DESCRICAO_PERFIL = {
    "Conservador": (
        "Prioriza proteger o capital. Aceita pouca oscilação e prefere sair "
        "cedo de uma posição a correr risco de perda grande."
    ),
    "Moderado": (
        "Busca equilíbrio entre proteção e crescimento. Aceita oscilações "
        "moderadas em troca de um retorno melhor no médio prazo."
    ),
    "Arrojado": (
        "Prioriza o crescimento do patrimônio e aceita oscilações fortes e "
        "perdas temporárias relevantes em busca de ganhos maiores."
    ),
}

FONTES = [
    {
        "titulo": "Resolução CVM nº 30, de 11/05/2021 (suitability), art. 3º e 4º",
        "url": "https://conteudo.cvm.gov.br/legislacao/resolucoes/resol030.html",
    },
    {
        "titulo": "ANBIMA: novas regras de suitability, incluindo conhecimento e criptoativos (05/09/2023)",
        "url": "https://www.anbima.com.br/pt_br/noticias/suitability-novas-regras-da-anbima-para-definir-perfis-de-investidor-e-de-produtos-ja-estao-valendo.htm",
    },
]

EIXOS = {
    "objetivos": "Objetivos",
    "situacao_financeira": "Situação financeira",
    "conhecimento": "Conhecimento",
}

# Cada pergunta: id, eixo da CVM 30, base (artigo), texto e as opções na ordem
# do menos ao mais arrojado (o índice 0 vale 1 ponto, o 3 vale 4).
PERGUNTAS = [
    {
        "id": "objetivo", "eixo": "objetivos", "base": "Res. CVM 30, art. 3º, § 1º, III (finalidades)",
        "texto": "Qual é o seu principal objetivo ao investir em criptomoedas?",
        "opcoes": [
            "Proteger o dinheiro que já tenho",
            "Ter uma renda complementar",
            "Fazer o patrimônio crescer no longo prazo",
            "Maximizar o ganho, mesmo com risco alto",
        ],
    },
    {
        "id": "horizonte", "eixo": "objetivos", "base": "Res. CVM 30, art. 3º, § 1º, I (período)",
        "texto": "Por quanto tempo você pretende manter esse investimento?",
        "opcoes": ["Menos de 1 ano", "De 1 a 3 anos", "De 3 a 5 anos", "Mais de 5 anos"],
    },
    {
        "id": "queda", "eixo": "objetivos", "base": "Res. CVM 30, art. 3º, § 1º, II (preferências de risco)",
        "texto": "Seu investimento cai 20% em um mês. O que você faz?",
        "opcoes": [
            "Vendo tudo para não perder mais",
            "Vendo uma parte",
            "Mantenho e espero recuperar",
            "Aproveito para comprar mais",
        ],
    },
    {
        "id": "perda_maxima", "eixo": "objetivos", "base": "Res. CVM 30, art. 3º, § 1º, II (preferências de risco)",
        "texto": "Qual perda você aceitaria ter em um ano, sem desistir?",
        "opcoes": ["Até 5%", "Até 15%", "Até 30%", "Mais de 30%"],
    },
    {
        "id": "renda", "eixo": "situacao_financeira", "base": "Res. CVM 30, art. 3º, § 2º, I (receitas regulares)",
        "texto": "Sua renda mensal regular cobre os seus gastos?",
        "opcoes": [
            "Não cobre meus gastos",
            "Cobre meus gastos, sem sobra",
            "Cobre meus gastos e sobra um pouco",
            "Cobre meus gastos com folga e sobra bastante",
        ],
    },
    {
        "id": "parcela", "eixo": "situacao_financeira", "base": "Res. CVM 30, art. 3º, § 2º, II (patrimônio)",
        "texto": "Que parte do seu patrimônio você pretende colocar em criptomoedas?",
        "opcoes": ["Menos de 5%", "De 5% a 15%", "De 15% a 30%", "Mais de 30%"],
    },
    {
        "id": "reserva", "eixo": "situacao_financeira", "base": "Res. CVM 30, art. 3º, § 2º, II (ativos do patrimônio)",
        "texto": "Você tem uma reserva de emergência (fora desse investimento)?",
        "opcoes": [
            "Não tenho",
            "Cobre até 3 meses de gastos",
            "Cobre de 3 a 6 meses",
            "Cobre mais de 6 meses",
        ],
    },
    {
        "id": "dependencia", "eixo": "situacao_financeira", "base": "Res. CVM 30, art. 3º, § 2º, III (necessidade futura de recursos)",
        "texto": "Você vai precisar desse dinheiro em breve?",
        "opcoes": [
            "Sim, preciso dele em breve",
            "Talvez precise",
            "Dificilmente vou precisar",
            "Não vou precisar",
        ],
    },
    {
        "id": "experiencia", "eixo": "conhecimento", "base": "Res. CVM 30, art. 3º, § 3º, I (familiaridade com produtos)",
        "texto": "Com quais investimentos você tem familiaridade?",
        "opcoes": [
            "Nenhum, nunca investi",
            "Só poupança ou renda fixa",
            "Também ações ou fundos",
            "Também criptomoedas ou derivativos",
        ],
    },
    {
        "id": "operacoes", "eixo": "conhecimento", "base": "Res. CVM 30, art. 3º, § 3º, II (natureza, volume e frequência)",
        "texto": "Com que frequência você faz operações de compra e venda de investimentos?",
        "opcoes": [
            "Nunca fiz operações",
            "Fiz poucas vezes",
            "Faço algumas vezes por ano",
            "Faço todo mês ou com mais frequência",
        ],
    },
    {
        "id": "formacao", "eixo": "conhecimento", "base": "Res. CVM 30, art. 3º, § 3º, III (formação e experiência profissional)",
        "texto": "Sua formação ou profissão tem relação com finanças e investimentos?",
        "opcoes": [
            "Nenhuma relação",
            "Fiz cursos ou estudo por conta própria",
            "Tenho formação ou trabalho parcialmente na área",
            "Tenho formação ou atuação profissional na área",
        ],
    },
]

PONTUACAO_MAXIMA = 4 * len(PERGUNTAS)
PONTUACAO_MINIMA = len(PERGUNTAS)
# Terços da faixa de pontos (de 11 a 44): 11-22, 23-33, 34-44.
LIMITE_CONSERVADOR = PONTUACAO_MINIMA + (PONTUACAO_MAXIMA - PONTUACAO_MINIMA) // 3
LIMITE_MODERADO = PONTUACAO_MINIMA + 2 * (PONTUACAO_MAXIMA - PONTUACAO_MINIMA) // 3


class ErroQuestionario(ValueError):
    """Respostas ausentes ou inválidas."""


def _perfil_pela_pontuacao(pontos: int) -> str:
    if pontos <= LIMITE_CONSERVADOR:
        return "Conservador"
    if pontos <= LIMITE_MODERADO:
        return "Moderado"
    return "Arrojado"


def _por_eixo(pontos_por_pergunta: dict[str, int]) -> dict[str, dict]:
    eixos = {}
    for chave, nome in EIXOS.items():
        ids = [p["id"] for p in PERGUNTAS if p["eixo"] == chave]
        eixos[chave] = {
            "nome": nome,
            "pontos": sum(pontos_por_pergunta[i] for i in ids),
            "maximo": 4 * len(ids),
        }
    return eixos


def calcular_perfil(respostas: dict[str, int]) -> dict:
    """Valida as respostas e devolve pontuação, perfil sugerido e travas.

    ``respostas`` mapeia o id da pergunta ao índice (0 a 3) da opção escolhida.
    """
    if not isinstance(respostas, dict):
        raise ErroQuestionario("Envie as respostas do questionário.")

    pontos_por_pergunta: dict[str, int] = {}
    for pergunta in PERGUNTAS:
        escolhida = respostas.get(pergunta["id"])
        if (
            isinstance(escolhida, bool)
            or not isinstance(escolhida, int)
            or not 0 <= escolhida < len(pergunta["opcoes"])
        ):
            raise ErroQuestionario(f"Responda a pergunta '{pergunta['id']}'.")
        pontos_por_pergunta[pergunta["id"]] = escolhida + 1

    pontuacao = sum(pontos_por_pergunta.values())
    perfil = _perfil_pela_pontuacao(pontuacao)

    # Travas: fragilidades que uma boa pontuação não pode esconder. Uma limita
    # o perfil a Moderado; duas ou mais, a Conservador.
    fragilidades = []
    if pontos_por_pergunta["reserva"] == 1:
        fragilidades.append("não ter reserva de emergência")
    if pontos_por_pergunta["dependencia"] == 1:
        fragilidades.append("precisar do dinheiro em breve")
    if pontos_por_pergunta["experiencia"] == 1 and pontos_por_pergunta["operacoes"] == 1:
        fragilidades.append("ainda não ter experiência com investimentos")

    travas: list[str] = []
    teto = "Conservador" if len(fragilidades) >= 2 else "Moderado" if fragilidades else None
    if teto and PERFIS.index(perfil) > PERFIS.index(teto):
        travas.append(
            f"Por {' e por '.join(fragilidades)}, o perfil foi limitado a {teto}."
        )
        perfil = teto

    return {
        "perfil": perfil,
        "pontuacao": pontuacao,
        "pontuacao_maxima": PONTUACAO_MAXIMA,
        "por_eixo": _por_eixo(pontos_por_pergunta),
        "travas": travas,
        "descricao": DESCRICAO_PERFIL[perfil],
    }


def validar_perfil(perfil: str) -> str:
    if perfil not in PERFIS:
        raise ErroQuestionario(f"Perfil inválido. Use: {', '.join(PERFIS)}.")
    return perfil


def serializar_respostas(respostas: dict[str, int]) -> str:
    return json.dumps(respostas, ensure_ascii=False, sort_keys=True)
