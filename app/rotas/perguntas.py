# Rota de /processos/{id}/perguntar: assistente de perguntas em linguagem
# natural (Fase 2, Camada 1) — recebe uma pergunta em texto livre e devolve
# a resposta da IA, montada por cima do texto bruto salvo por página
# (Camada 0). Igual ao resto das rotas: o trabalho de verdade mora em
# app/pipeline.py, esta rota só converte HTTP <-> chamada de função.

from fastapi import APIRouter
from pydantic import BaseModel

from app.pipeline import responder_pergunta_processo

router = APIRouter()


class PerguntaBody(BaseModel):
    pergunta: str


@router.post("/processos/{id}/perguntar")
def perguntar_rota(id: int, body: PerguntaBody) -> dict:
    return responder_pergunta_processo(id, body.pergunta)
