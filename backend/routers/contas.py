"""Contas, sessões e perfil de investimento."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db
from models import Usuario
from routers.deps import token_do_cabecalho, usuario_atual, usuario_json
from schemas import CadastroRequest, LoginRequest, PerfilRequest, QuestionarioRequest
from services import contas as svc_contas
from services import perfil as svc_perfil
from utils.tempo import agora_utc

logger = logging.getLogger("sentcrypto")

router = APIRouter()

limitador_login = svc_contas.LimitadorTentativas(maximo=5, janela_s=600)


@router.post("/auth/cadastro", tags=["contas"], status_code=201)
def cadastrar(body: CadastroRequest, db: Session = Depends(get_db)):
    """Cria a conta e já entra nela."""
    try:
        usuario = svc_contas.criar_usuario(db, body.nome, body.email, body.senha)
    except svc_contas.ErroConta as e:
        raise HTTPException(status_code=e.status, detail=e.mensagem) from e
    token = svc_contas.criar_sessao(db, usuario)
    logger.info("Conta criada (id %s).", usuario.id)
    return {"token": token, "usuario": usuario_json(usuario)}


@router.post("/auth/login", tags=["contas"])
def entrar(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    chave = f"{(body.email or '').strip().lower()}|{ip}"
    if limitador_login.bloqueado(chave):
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas. Aguarde alguns minutos e tente de novo.",
        )
    usuario = svc_contas.autenticar(db, body.email, body.senha)
    if usuario is None:
        limitador_login.registrar_falha(chave)
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")
    limitador_login.limpar(chave)
    return {
        "token": svc_contas.criar_sessao(db, usuario),
        "usuario": usuario_json(usuario),
    }


@router.post("/auth/logout", tags=["contas"])
def sair(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    svc_contas.encerrar_sessao(db, token_do_cabecalho(authorization))
    return {"sucesso": True}


@router.get("/auth/eu", tags=["contas"])
def quem_sou_eu(usuario: Usuario = Depends(usuario_atual)):
    return usuario_json(usuario)


@router.get("/perfil/questionario", tags=["perfil"])
def questionario():
    """Perguntas e opções do questionário (uma única fonte para o front)."""
    return {
        "perguntas": svc_perfil.PERGUNTAS,
        "eixos": svc_perfil.EIXOS,
        "fontes": svc_perfil.FONTES,
        "perfis": [
            {"nome": nome, "descricao": svc_perfil.DESCRICAO_PERFIL[nome]}
            for nome in svc_perfil.PERFIS
        ],
    }


@router.post("/perfil/questionario", tags=["perfil"])
def responder_questionario(
    body: QuestionarioRequest,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
):
    """Calcula o perfil sugerido. A pessoa ainda precisa confirmá-lo."""
    try:
        resultado = svc_perfil.calcular_perfil(body.respostas)
    except svc_perfil.ErroQuestionario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    usuario.perfil_sugerido = resultado["perfil"]
    usuario.pontuacao = resultado["pontuacao"]
    usuario.respostas = svc_perfil.serializar_respostas(body.respostas)
    db.commit()
    return resultado


@router.post("/perfil/confirmar", tags=["perfil"])
def confirmar_perfil(
    body: PerfilRequest,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
):
    """Grava o perfil escolhido (o sugerido ou outro que a pessoa prefira)."""
    try:
        usuario.perfil = svc_perfil.validar_perfil(body.perfil)
    except svc_perfil.ErroQuestionario as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    usuario.perfil_atualizado_em = agora_utc()
    db.commit()
    return usuario_json(usuario)
