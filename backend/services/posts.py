"""Persistência dos posts sociais já classificados.

Concentra a regra de "analisar com BERT e salvar sem duplicar", que antes
estava embutida na rota e não tinha proteção contra reprocessamento.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import IDADE_MAXIMA_POST_DIAS
from models import PostDescartado, SocialPost
from services import sentimento as svc_sentimento
from utils.tempo import agora_utc, para_utc_naive

logger = logging.getLogger("sentcrypto.posts")


@dataclass
class ResultadoColeta:
    """Contagens de uma rodada de coleta."""

    recebidos: int = 0
    salvos: int = 0
    duplicados: int = 0
    erros: int = 0
    # Posts lidos que não citam a moeda (novos nesta rodada).
    descartados: int = 0
    # Posts lidos e ignorados por serem antigos demais (ver separar_antigos).
    antigos: int = 0

    @property
    def mensagem(self) -> str:
        texto = (
            f"{self.salvos} novos posts salvos, "
            f"{self.duplicados} já existiam, "
            f"{self.erros} com erro (de {self.recebidos} coletados)."
        )
        if self.descartados:
            texto += f" {self.descartados} sem relação com a moeda descartados."
        if self.antigos:
            texto += (f" {self.antigos} com mais de {IDADE_MAXIMA_POST_DIAS} dias "
                      "ignorados.")
        return texto

    def como_dict(self) -> dict:
        return {
            "mensagem": self.mensagem,
            "total_coletados": self.recebidos,
            "salvos": self.salvos,
            "duplicados": self.duplicados,
            "erros": self.erros,
            "descartados": self.descartados,
            "antigos": self.antigos,
        }


def separar_antigos(
    brutos: list[dict], dias: int = IDADE_MAXIMA_POST_DIAS, agora=None,
) -> tuple[list[dict], int]:
    """Tira os posts publicados há mais de ``dias`` dias.

    Devolve ``(recentes, quantos_sairam)``. Posts sem data ficam (não há
    como saber a idade); ``dias <= 0`` desliga o filtro.
    """
    if not dias or dias <= 0:
        return list(brutos), 0
    limite = (agora or agora_utc()) - timedelta(days=dias)
    recentes = [
        b for b in brutos
        if not b.get("timestamp_post") or para_utc_naive(b["timestamp_post"]) >= limite
    ]
    return recentes, len(brutos) - len(recentes)


def extrair_id(bruto: dict) -> str | None:
    """Identificador do post na origem, seja qual for o coletor.

    O coletor do Reddit entrega ``external_id``; o do X entrega ``tweet_id``.
    Normalizar aqui é o que faz a consulta de deduplicação enxergar os dois —
    sem isso ela buscava uma lista de ``None`` e nunca encontrava nada,
    deixando a proteção contra duplicatas depender só do fallback.
    """
    valor = bruto.get("external_id") or bruto.get("tweet_id")
    return str(valor) if valor else None


def ids_existentes(db: Session, fonte: str, external_ids: list[str]) -> set[str]:
    """Consulta em lote quais ``external_id`` dessa fonte já estão no banco."""
    ids_validos = [i for i in external_ids if i]
    if not ids_validos:
        return set()

    encontrados = db.execute(
        select(SocialPost.external_id).where(
            SocialPost.fonte == fonte,
            SocialPost.external_id.in_(ids_validos),
        )
    ).scalars()

    return set(encontrados)


def salvar_posts(
    db: Session,
    moeda: str,
    fonte: str,
    posts_brutos: list[dict],
) -> ResultadoColeta:
    """Classifica e grava uma lista de posts coletados.

    Posts cujo ``external_id`` já existe são pulados antes de chegar ao BERT —
    isso evita tanto a duplicação de dados quanto o custo de reprocessar
    inferência que já foi feita.
    """
    resultado = ResultadoColeta(recebidos=len(posts_brutos))
    if not posts_brutos:
        return resultado

    moeda_u = moeda.upper()

    # Deduplicação contra o banco, em uma única consulta.
    ja_salvos = ids_existentes(
        db, fonte, [extrair_id(p) for p in posts_brutos]
    )
    # Deduplicação dentro do próprio lote (o mesmo tweet pode vir de 2 perfis).
    vistos_no_lote: set[str] = set()

    novos: list[SocialPost] = []

    for bruto in posts_brutos:
        external_id = extrair_id(bruto)

        if external_id:
            if external_id in ja_salvos or external_id in vistos_no_lote:
                resultado.duplicados += 1
                continue
            vistos_no_lote.add(external_id)

        texto = (bruto.get("texto") or "").strip()
        if not texto:
            resultado.erros += 1
            logger.debug("Post sem texto ignorado: %r", bruto.get("url"))
            continue

        try:
            analise = svc_sentimento.classificar(texto)
        except Exception as e:
            # Antes, qualquer falha aqui era engolida por um `except: pass`,
            # tornando impossível descobrir por que os posts sumiam.
            resultado.erros += 1
            logger.warning(
                "Falha ao classificar post %s (%s): %s", external_id, fonte, e
            )
            continue

        novos.append(
            SocialPost(
                moeda=moeda_u,
                fonte=fonte,
                external_id=external_id,
                url=bruto.get("url"),
                autor=bruto.get("autor") or bruto.get("perfil"),
                texto=texto,
                sentimento=analise["sentimento"],
                score=analise["score"],
                timestamp_post=para_utc_naive(bruto.get("timestamp_post")),
            )
        )

    if novos:
        db.add_all(novos)
        try:
            db.commit()
            resultado.salvos = len(novos)
        except Exception as e:
            db.rollback()
            logger.error("Erro ao gravar lote de posts: %s", e)
            resultado.salvos = _salvar_um_a_um(db, novos, resultado)

    logger.info("Coleta %s/%s: %s", fonte, moeda_u, resultado.mensagem)
    return resultado


def _salvar_um_a_um(
    db: Session, posts: list[SocialPost], resultado: ResultadoColeta
) -> int:
    """Fallback: se o lote falhou, tenta cada post isoladamente.

    Uma única violação de constraint (ex.: coleta concorrente inserindo o
    mesmo tweet) faria o lote inteiro ser perdido. Aqui só o post problemático
    é descartado.
    """
    salvos = 0
    for post in posts:
        try:
            db.add(post)
            db.commit()
            salvos += 1
        except Exception as e:
            db.rollback()
            resultado.duplicados += 1
            logger.debug("Post %s não gravado: %s", post.external_id, e)
    return salvos


def registrar_descartados(
    db: Session, moeda: str, fonte: str, brutos: list[dict]
) -> int:
    """Conta os posts lidos e descartados pela triagem; devolve quantos são novos.

    Só a identidade do post é gravada. Posts sem identificador ou data ficam
    de fora: sem ``external_id`` a mesma leitura seria contada a cada rodada.
    """
    moeda_u = moeda.upper()
    por_id: dict[str, dict] = {}
    for bruto in brutos:
        external_id = extrair_id(bruto)
        if external_id and bruto.get("timestamp_post"):
            por_id.setdefault(external_id, bruto)
    if not por_id:
        return 0

    # A contagem é auxiliar: nenhuma falha aqui pode derrubar a coleta.
    try:
        ja_contados = set(db.execute(
            select(PostDescartado.external_id).where(
                PostDescartado.fonte == fonte,
                PostDescartado.moeda == moeda_u,
                PostDescartado.external_id.in_(list(por_id)),
            )
        ).scalars())
    except Exception as e:
        db.rollback()
        logger.warning("Falha ao consultar descartados (%s/%s): %s", fonte, moeda_u, e)
        return 0

    novos = [
        PostDescartado(
            moeda=moeda_u,
            fonte=fonte,
            external_id=external_id,
            autor=bruto.get("autor") or bruto.get("perfil"),
            timestamp_post=para_utc_naive(bruto["timestamp_post"]),
        )
        for external_id, bruto in por_id.items()
        if external_id not in ja_contados
    ]
    if not novos:
        return 0

    db.add_all(novos)
    try:
        db.commit()
    except Exception as e:
        # A contagem é auxiliar: falhar aqui não pode derrubar a coleta.
        db.rollback()
        logger.warning("Falha ao registrar descartados (%s/%s): %s", fonte, moeda_u, e)
        return 0
    return len(novos)


def volume_analisado(
    db: Session, moeda: str, fonte: str, inicio=None, fim=None
) -> dict:
    """Quantos posts passaram pela triagem e quantos foram aproveitados.

    ``analisados`` = posts salvos (relevantes + não-cripto) + descartados por
    não citarem a moeda. A contagem de descartados só existe a partir de
    ``contagem_desde``: antes disso eles não eram registrados.
    """
    def filtrar(query, modelo):
        query = query.filter(modelo.moeda == moeda.upper(), modelo.fonte == fonte)
        if inicio:
            query = query.filter(modelo.timestamp_post >= inicio)
        if fim:
            query = query.filter(modelo.timestamp_post <= fim)
        return query

    salvos = filtrar(db.query(func.count(SocialPost.id)), SocialPost).scalar() or 0
    relevantes = filtrar(
        db.query(func.count(SocialPost.id)), SocialPost
    ).filter(SocialPost.sentimento != svc_sentimento.NULO).scalar() or 0
    descartados = filtrar(
        db.query(func.count(PostDescartado.id)), PostDescartado
    ).scalar() or 0
    desde = db.query(func.min(PostDescartado.timestamp_coleta)).filter(
        PostDescartado.moeda == moeda.upper(), PostDescartado.fonte == fonte,
    ).scalar()

    return {
        "analisados": salvos + descartados,
        "relevantes": relevantes,
        "nao_cripto": salvos - relevantes,
        "sem_a_moeda": descartados,
        "descartados": descartados + salvos - relevantes,
        "contagem_desde": desde.isoformat() + "Z" if desde else None,
    }
