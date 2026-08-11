# Rota de /exigencias: só a atualização de status/observação (o clique do
# checkbox, que o template do Passo 7 vai chamar).

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.repositorio import atualizar_status_exigencia

router = APIRouter()


class AtualizarStatusBody(BaseModel):
    status_check: str
    observacao: str | None = None


@router.patch("/exigencias/{id}")
def atualizar_status_exigencia_rota(id: int, body: AtualizarStatusBody) -> dict:
    return atualizar_status_exigencia(id, body.status_check, body.observacao)
