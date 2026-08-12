# Gera demo.db -- o banco SQLite fixo que a instância pública (modo
# estático, NEXLICIT_DEMO_ESTATICO=1, ver app/demo_estatico.py) usa pra
# mostrar o processo fictício já analisado, sem depender de nenhuma
# chamada de IA em tempo de execução.
#
# Roda o pipeline REAL (extração + checklist + Camada 3, sem stub nenhum)
# contra o edital fictício desta mesma pasta -- 3 chamadas de
# generate_content da cota diária do Gemini (20/dia). Não faz parte do
# deploy em si: roda uma vez aqui, o resultado (demo.db) é comitado (ver
# a exceção específica pra este arquivo em .gitignore).
#
# Nunca toca no banco real: caminho_banco vai explícito em toda chamada,
# mesmo princípio de qualquer script avulso do projeto (ver
# app/db/conexao.py e a trava NEXLICIT_USE_BANCO_REAL).
#
# Pra regenerar: python demo/edital_ficticio/gerar_demo_db.py
# (a partir da raiz do projeto, com o venv ativado)

import os
import sys

_RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _RAIZ_PROJETO)

from app.db.repositorio import criar_processo, obter_inconsistencias, obter_processo  # noqa: E402
from app.pipeline import processar_processo  # noqa: E402

PASTA = os.path.dirname(os.path.abspath(__file__))
CAMINHO_PDF = os.path.join(PASTA, "Edital_Ficticio_Pregao_01-2026_Exemplopolis.pdf")
CAMINHO_DB = os.path.join(PASTA, "demo.db")

if os.path.exists(CAMINHO_DB):
    # Regeneração idempotente: sem isso, rodar de novo criaria um SEGUNDO
    # processo (id 2) no mesmo banco, em vez de substituir o primeiro --
    # o demo público precisa mostrar exatamente 1 processo fixo.
    os.remove(CAMINHO_DB)

processo_id = criar_processo(
    {
        "nome": "Exemplópolis PE 01/2026 (FICTÍCIO - demo)",
        "orgao": "Município de Exemplópolis",
        "modalidade": "Pregão Eletrônico",
        "objeto": "Aquisição de materiais de limpeza e higienização",
    },
    caminho_banco=CAMINHO_DB,
)

resumo = processar_processo(processo_id, [CAMINHO_PDF], caminho_banco=CAMINHO_DB)

processo = obter_processo(processo_id, caminho_banco=CAMINHO_DB)
assert processo is not None, "processo recém-criado deveria existir"
achados = obter_inconsistencias(processo_id, caminho_banco=CAMINHO_DB)

print(f"demo.db gerado: {CAMINHO_DB}")
print(f"processo_id: {processo_id}")
print(f"resumo: {resumo}")
print(f"inconsistencias_comparacao_possivel: {processo['inconsistencias_comparacao_possivel']}")
print(f"achados: {len(achados)}")
for achado in achados:
    print(f"  - [{achado['tipo']}] {achado['descricao']}")
