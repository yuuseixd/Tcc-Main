"""Dependências e helpers compartilhados pelas rotas."""

from datetime import datetime

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from config import ADMIN_TOKEN, MOEDAS_SUPORTADAS
from db import get_db
from models import SocialPost, Usuario
from services import contas as svc_contas
from services import mercado as svc_mercado
from services import sentimento as svc_sentimento
from utils.tempo import para_iso_utc, parse_data_fim, parse_data_inicio, truncar_hora


def validar_moeda(moeda: str) -> str:
    """Normaliza e recusa moedas fora da lista suportada.

    Sem isso, um símbolo inválido só falhava lá na Binance, devolvendo um 502
    confuso em vez de dizer que a moeda não é aceita.
    """
    moeda_u = (moeda or "").strip().upper()
    if moeda_u not in MOEDAS_SUPORTADAS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Moeda '{moeda}' não suportada. "
                f"Disponíveis: {', '.join(MOEDAS_SUPORTADAS)}."
            ),
        )
    return moeda_u


def validar_fonte(fonte: str) -> str:
    """Normaliza a fonte social para os valores gravados no banco."""
    mapa = {"x": "X", "twitter": "X", "reddit": "Reddit"}
    normalizada = mapa.get((fonte or "").strip().lower())
    if not normalizada:
        raise HTTPException(
            status_code=400, detail="Fonte inválida. Use 'X' ou 'Reddit'."
        )
    return normalizada


def exigir_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Protege rotas que alteram credenciais.

    Se ``ADMIN_TOKEN`` não estiver configurado, a rota continua aberta — o
    padrão de desenvolvimento local. Em qualquer exposição de rede, definir
    ADMIN_TOKEN no .env passa a ser obrigatório.
    """
    if not ADMIN_TOKEN:
        return
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token administrativo inválido.")


def token_do_cabecalho(authorization: str | None) -> str | None:
    if not authorization:
        return None
    esquema, _, token = authorization.partition(" ")
    return token.strip() if esquema.lower() == "bearer" and token.strip() else None


def usuario_atual(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Usuario:
    """Exige login: devolve o usuário da sessão ou responde 401.

    A Visão geral é a única área aberta; as demais rotas usam esta dependência
    (no front-end o bloqueio é só conforto, quem protege de verdade é o servidor).
    """
    usuario = svc_contas.usuario_da_sessao(db, token_do_cabecalho(authorization))
    if usuario is None:
        raise HTTPException(
            status_code=401,
            detail="Entre na sua conta para usar este recurso.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


def usuario_json(u: Usuario) -> dict:
    return {
        "id": u.id,
        "nome": u.nome,
        "email": u.email,
        "perfil": u.perfil,
        "perfil_sugerido": u.perfil_sugerido,
        "pontuacao": u.pontuacao,
        "criado_em": para_iso_utc(u.criado_em) if u.criado_em else None,
    }


def buscar_klines_ou_502(simbolo: str, **kwargs) -> list:
    try:
        return svc_mercado.buscar_klines(simbolo, **kwargs)
    except svc_mercado.ErroBinance as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


def parse_periodo(
    data_inicio: str | None, data_fim: str | None
) -> tuple[datetime | None, datetime | None]:
    """Valida o período opcional das análises (qualquer um dos lados pode faltar)."""
    inicio = parse_data_inicio(data_inicio)
    fim = parse_data_fim(data_fim)
    if (data_inicio and inicio is None) or (data_fim and fim is None):
        raise HTTPException(
            status_code=400, detail="Data inválida. Use o formato AAAA-MM-DD."
        )
    if inicio and fim and inicio > fim:
        raise HTTPException(
            status_code=400,
            detail="Período inválido: a data inicial é posterior à final.",
        )
    return inicio, fim


def parse_hora_obrigatoria(hora: str) -> datetime:
    dt = parse_data_inicio(hora)
    if dt is None:
        raise HTTPException(
            status_code=400, detail="Formato de hora inválido. Use ISO 8601."
        )
    return truncar_hora(dt)


def posts_da_hora(
    db: Session, moeda: str, fonte: str, hora_inicio: datetime
) -> list[SocialPost]:
    from datetime import timedelta

    # Intervalo semiaberto [hora, hora+1h): evita depender de microssegundos
    # para não incluir/excluir posts na virada da hora.
    hora_fim = hora_inicio + timedelta(hours=1)
    return (
        db.query(SocialPost)
        .filter(
            SocialPost.moeda == moeda,
            SocialPost.fonte == fonte,
            SocialPost.timestamp_post >= hora_inicio,
            SocialPost.timestamp_post < hora_fim,
        )
        .order_by(SocialPost.timestamp_post.asc())
        .all()
    )


def exigir_bert() -> None:
    if not svc_sentimento.modelo_carregado():
        raise HTTPException(
            status_code=503,
            detail="Modelo BERT não carregado. Verifique os logs do servidor.",
        )
