# Rota de /processos/{id}/detectar-inconsistencias: motor de
# inconsistências edital-vs-TR (Fase 2, segunda metade), Camada 1. Manual
# por enquanto (não é chamada pela análise automática) — só o clique/chamada
# direta dispara, até a qualidade da detecção estar validada (mesma cautela
# do Q&A: testar antes de automatizar, testar antes de construir UI).
#
# Igual ao resto das rotas: o trabalho de verdade mora em app/pipeline.py,
# esta rota só converte HTTP <-> chamada de função. Sem corpo de requisição
# — o processo já tem tudo que a comparação precisa (texto_pagina).

from fastapi import APIRouter

from app.demo_estatico import bloquear_se_demo_estatico
from app.pipeline import detectar_inconsistencias_processo

router = APIRouter()


@router.post("/processos/{id}/detectar-inconsistencias")
def detectar_inconsistencias_rota(id: int) -> dict:
    bloquear_se_demo_estatico()
    return detectar_inconsistencias_processo(id)
