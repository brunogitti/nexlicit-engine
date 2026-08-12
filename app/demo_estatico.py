# Bloqueio das rotas de escrita quando o modo estático do demo público
# está ativo (app.config.DEMO_ESTATICO, ver o comentário lá pro porquê).
#
# Módulo próprio, não misturado em app/config.py: mesmo padrão do resto do
# projeto (app/pipeline.py e app/ia/llm_client.py têm cada um suas
# próprias exceções de domínio) — app/config.py só lê variável de
# ambiente, quem usa o valor mora em outro lugar.

from app.config import DEMO_ESTATICO


class ModoDemoEstaticoError(Exception):
    """Levantada por uma rota de escrita quando DEMO_ESTATICO está ativo.
    Tratada em app/erros.py -> HTTP 403."""


def bloquear_se_demo_estatico() -> None:
    """Chamado no início de toda rota de escrita (criar processo, analisar,
    perguntar, detectar inconsistências). Levanta ModoDemoEstaticoError se
    o modo estático estiver ativo — quem chama não precisa checar o valor
    da flag, só deixar a exceção subir pro tratador central
    (app/erros.py), igual a qualquer outra exceção de domínio do projeto.
    """
    if DEMO_ESTATICO:
        raise ModoDemoEstaticoError(
            "esta é uma instância de demonstração pública; o resultado "
            "exibido já foi processado previamente e não pode ser "
            "alterado aqui. Nenhuma ação de escrita está disponível nesta "
            "instância (criar processo, analisar, perguntar, verificar "
            "inconsistências)."
        )
