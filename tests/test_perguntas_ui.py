# Testes de ponta a ponta da seção de perguntas em linguagem natural (Fase
# 2, Camada 2), via Playwright contra um servidor uvicorn REAL — TestClient
# (usado no resto da suíte) não executa JavaScript, e é o JS de
# app/static/js/perguntas.js que monta o card de resposta na tela. Sem
# esse tipo de teste, a lógica de fetch/DOM do arquivo ficaria sem nenhuma
# cobertura automatizada.
#
# Usa o canal "msedge" (Edge já instalado no sistema) em vez de baixar um
# Chromium novo do Playwright — decisão registrada no relatório da Camada
# 2, aprovada explicitamente.
#
# Cada teste sobe um servidor próprio numa thread, apontando pro banco de
# teste (monkeypatch em app.db.conexao.DATABASE_PATH, mesmo padrão do
# cliente_teste de test_paginas.py) — nunca o nexlicit.db real. A resposta
# da IA é sempre fake (monkeypatch em app.pipeline._responder_pergunta_ia,
# mesmo padrão de test_rotas.py), então nenhum teste aqui gasta quota do
# Gemini nem depende de rede externa.

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.db.repositorio import criar_arquivo, criar_processo, salvar_texto_paginas
from app.ia.llm_client import ContextoGrandeDemaisError


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    caminho_db = str(tmp_path / "teste.db")
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_db)
    # Servidor real dispara o lifespan do FastAPI de verdade (a limpeza
    # automática de processos antigos, 16/08/2026) -- sem isolar o log de
    # auditoria também, cada rodada da suíte escreveria no
    # logs/limpeza_automatica.log de verdade.
    monkeypatch.setattr("app.limpeza.CAMINHO_LOG_AUDITORIA", str(tmp_path / "limpeza.log"))

    from app.main import app

    porta = _porta_livre()
    config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Espera o servidor aceitar conexão de verdade, sem sleep fixo — mais
    # rápido quando a máquina está livre, e não falha à toa quando está
    # ocupada.
    for _ in range(200):
        try:
            httpx.get(f"http://127.0.0.1:{porta}/", timeout=0.5)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    else:
        raise RuntimeError("servidor de teste não subiu a tempo")

    yield f"http://127.0.0.1:{porta}", caminho_db

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def navegador():
    with sync_playwright() as playwright:
        instancia = playwright.chromium.launch(channel="msedge", headless=True)
        yield instancia
        instancia.close()


def _criar_processo_com_texto(caminho_db) -> int:
    """Processo mínimo com uma página de texto — o suficiente pra
    /perguntar não recusar por "sem análise" (ProcessoSemTextoExtraidoError,
    ver app/pipeline.py). O conteúdo da resposta em si vem do fake de
    _responder_pergunta_ia, não deste texto."""
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        [{"numero_pagina": 1, "localizador": "página 1", "texto": "Texto de exemplo do edital."}],
        caminho_banco=caminho_db,
    )
    return processo_id


def test_pergunta_com_resposta_encontrada_mostra_citacao(servidor, navegador, monkeypatch):
    base_url, caminho_db = servidor
    processo_id = _criar_processo_com_texto(caminho_db)

    def ia_falsa(texto_completo_marcado, pergunta):
        return {
            "encontrado": True,
            "resposta": "O prazo de validade da proposta é de 60 dias.",
            "paginas": [12, 38],
        }

    monkeypatch.setattr("app.pipeline._responder_pergunta_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")
        pagina.fill("#campo-pergunta", "Qual é o prazo de validade da proposta?")
        pagina.click("#form-pergunta button[type=submit]")

        card = pagina.wait_for_selector(".card-pergunta-resposta", timeout=5000)

        assert "Qual é o prazo de validade da proposta?" in card.inner_text()
        # A resposta encontrada reaproveita .trecho (mesmo estilo visual do
        # checklist), com as páginas citadas no <cite>.
        assert card.query_selector(".trecho") is not None
        assert "60 dias" in card.query_selector(".trecho p").inner_text()
        assert "Páginas: 12, 38" in card.query_selector(".trecho cite").inner_text()
        # Não é tratado como card negativo nem como aviso de limitação.
        assert card.query_selector(".resposta-nao-encontrada") is None
        assert card.query_selector(".aviso-limitacao") is None
        # O campo volta limpo e pronto pra próxima pergunta.
        assert pagina.input_value("#campo-pergunta") == ""
    finally:
        pagina.close()


def test_indicador_de_carregando_some_depois_da_resposta(servidor, navegador, monkeypatch):
    # Regressão: encontrado no teste visual real (Camada 2) — a regra
    # .pergunta-carregando{display:flex} (autor) vencia a regra padrão do
    # navegador [hidden]{display:none} (user-agent), então setar
    # carregando.hidden = true em perguntas.js não escondia nada de
    # verdade. "Buscando resposta..." ficava na tela por baixo da resposta
    # já carregada. Corrigido com .pergunta-carregando[hidden]{display:none}
    # em app/static/css/nexlicit.css.
    base_url, caminho_db = servidor
    processo_id = _criar_processo_com_texto(caminho_db)

    def ia_falsa(texto_completo_marcado, pergunta):
        return {"encontrado": True, "resposta": "Resposta qualquer.", "paginas": [1]}

    monkeypatch.setattr("app.pipeline._responder_pergunta_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")
        assert pagina.is_hidden("#pergunta-carregando")  # estado inicial, antes de qualquer pergunta

        pagina.fill("#campo-pergunta", "Pergunta qualquer")
        pagina.click("#form-pergunta button[type=submit]")
        pagina.wait_for_selector(".card-pergunta-resposta", timeout=5000)

        assert pagina.is_hidden("#pergunta-carregando")  # some de novo depois da resposta chegar
    finally:
        pagina.close()


def test_pergunta_sem_resposta_mostra_estado_negativo_distinto(servidor, navegador, monkeypatch):
    base_url, caminho_db = servidor
    processo_id = _criar_processo_com_texto(caminho_db)

    def ia_falsa(texto_completo_marcado, pergunta):
        return {
            "encontrado": False,
            "resposta": "Não localizei essa informação no texto fornecido.",
            "paginas": [],
        }

    monkeypatch.setattr("app.pipeline._responder_pergunta_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")
        pagina.fill("#campo-pergunta", "Qual é a cor do uniforme dos entregadores?")
        pagina.click("#form-pergunta button[type=submit]")

        card = pagina.wait_for_selector(".card-pergunta-resposta", timeout=5000)

        # Estado negativo visualmente distinto de uma citação normal — não
        # usa .trecho, usa .resposta-nao-encontrada (fundo/borda --stamp).
        assert card.query_selector(".trecho") is None
        negativo = card.query_selector(".resposta-nao-encontrada")
        assert negativo is not None
        # .lower(): o .eyebrow vira maiúsculo na tela via CSS
        # (text-transform), e inner_text() devolve o texto RENDERIZADO, não
        # o textContent original — comparar em minúsculas testa o
        # conteúdo, não a apresentação visual.
        assert "não localizado no edital" in negativo.inner_text().lower()
        assert "não localizei essa informação" in negativo.inner_text().lower()
        # Também não é tratado como erro de sistema nem como limitação de tamanho.
        assert pagina.is_hidden("#aviso-erro-pergunta")
        assert card.query_selector(".aviso-limitacao") is None
    finally:
        pagina.close()


def test_pergunta_com_contexto_grande_demais_mostra_mensagem_clara(servidor, navegador, monkeypatch):
    base_url, caminho_db = servidor
    processo_id = _criar_processo_com_texto(caminho_db)

    def ia_falsa(texto_completo_marcado, pergunta):
        raise ContextoGrandeDemaisError(
            "o texto deste processo tem aproximadamente 2.000.000 tokens, "
            "acima do limite de 1.043.576 tokens que o modelo suporta numa "
            "pergunta só."
        )

    monkeypatch.setattr("app.pipeline._responder_pergunta_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")
        pagina.fill("#campo-pergunta", "Qualquer pergunta")
        pagina.click("#form-pergunta button[type=submit]")

        card = pagina.wait_for_selector(".card-pergunta-resposta", timeout=5000)

        # Mensagem própria de limitação conhecida — não é o banner genérico
        # de erro de sistema (#aviso-erro-pergunta) nem o card de "não
        # encontrado".
        aviso = card.query_selector(".aviso-limitacao")
        assert aviso is not None
        assert "edital grande demais" in aviso.inner_text().lower()  # ver nota de .lower() acima
        assert "2.000.000 tokens" in aviso.inner_text()
        assert pagina.is_hidden("#aviso-erro-pergunta")
        assert card.query_selector(".resposta-nao-encontrada") is None
    finally:
        pagina.close()


def test_pergunta_com_erro_de_sistema_mostra_aviso_generico(servidor, navegador, monkeypatch):
    # A IA falha com algo que NÃO é ContextoGrandeDemaisError (aqui,
    # RespostaIAError -> HTTP 502, ver app/erros.py) — do ponto de vista da
    # UI isso é "algo quebrou de verdade", diferente das três respostas
    # válidas do produto (encontrada / não encontrada / edital grande
    # demais). Reaproveita o banner --stamp já usado pra falha de
    # salvamento no checklist, em vez de ganhar um card próprio.
    base_url, caminho_db = servidor
    processo_id = _criar_processo_com_texto(caminho_db)

    from app.ia.llm_client import RespostaIAError

    def ia_falsa(texto_completo_marcado, pergunta):
        raise RespostaIAError("falha ao chamar a API do Gemini: erro simulado no teste")

    monkeypatch.setattr("app.pipeline._responder_pergunta_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")
        pagina.fill("#campo-pergunta", "Qualquer pergunta")
        pagina.click("#form-pergunta button[type=submit]")

        pagina.wait_for_selector("#aviso-erro-pergunta:not([hidden])", timeout=5000)
        texto_aviso = pagina.inner_text("#aviso-erro-pergunta")
        assert texto_aviso.strip() != ""
        # Nenhum card foi criado pra esse tipo de falha.
        assert pagina.query_selector(".card-pergunta-resposta") is None
    finally:
        pagina.close()
