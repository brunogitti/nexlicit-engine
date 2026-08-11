# Testes de ponta a ponta do botão "Verificar inconsistências" (Fase 2,
# Camada 2), via Playwright contra um servidor uvicorn REAL — mesmo padrão
# de test_perguntas_ui.py (TestClient não executa JavaScript, e é o JS de
# app/static/js/inconsistencias.js que dispara o POST e recarrega a
# página).
#
# Diferente da seção de perguntas, o resultado aqui é PERSISTIDO (tabela
# inconsistencia + colunas inconsistencias_* em processo) — o teste
# confirma que, depois do clique, a página recarregada já vem do SERVIDOR
# com o estado novo renderizado (não é o JS que desenha o card).
#
# Usa o canal "msedge" (Edge já instalado no sistema), mesma decisão já
# registrada. Cada teste sobe um servidor próprio numa thread, apontando
# pro banco de teste — nunca o nexlicit.db real. A resposta da IA e a
# detecção da Camada 0 são sempre fake (monkeypatch em
# app.pipeline.identificar_blocos_edital_tr /
# app.pipeline._detectar_inconsistencias_ia), então nenhum teste aqui gasta
# quota do Gemini nem depende de rede externa.

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.db.repositorio import criar_processo
from app.inconsistencias.limite_tr import DeteccaoLimiteTR


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    caminho_db = str(tmp_path / "teste.db")
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_db)

    from app.main import app

    porta = _porta_livre()
    config = uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

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


_PAGINA_EDITAL_FALSA = {
    "numero_pagina": 5,
    "localizador": "página 5",
    "texto": "O prazo de entrega será de 30 dias.",
}
_PAGINA_TR_FALSA = {
    "numero_pagina": 20,
    "localizador": "página 20",
    "texto": "O prazo de entrega será de 45 dias.",
}


def test_botao_verificar_dispara_deteccao_e_recarrega_com_achados(servidor, navegador, monkeypatch):
    base_url, caminho_db = servidor
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )

    def ia_falsa(texto_edital_marcado, texto_tr_marcado):
        return [
            {
                "tipo": "prazo",
                "descricao": "prazo de entrega diverge entre edital e TR",
                "trecho_edital": "O prazo de entrega será de 30 dias.",
                "pagina_edital": 5,
                "trecho_tr": "O prazo de entrega será de 45 dias.",
                "pagina_tr": 20,
            }
        ]

    monkeypatch.setattr("app.pipeline.identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr("app.pipeline._detectar_inconsistencias_ia", ia_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")

        # Estado inicial: nunca verificado.
        assert "Ainda não verificado." in pagina.content()

        with pagina.expect_navigation(timeout=10000):
            pagina.click("#btn-verificar-inconsistencias")

        # Depois do reload, o HTML já vem do SERVIDOR com o achado —
        # não é o JS que monta isso (diferente da seção de perguntas).
        conteudo = pagina.content()
        assert "Ainda não verificado." not in conteudo
        assert "Prazo" in conteudo
        assert "O prazo de entrega será de 30 dias." in conteudo
        assert "O prazo de entrega será de 45 dias." in conteudo
        assert "página 5" in conteudo
        assert "página 20" in conteudo
    finally:
        pagina.close()


def test_botao_verificar_com_tr_nao_identificado_recarrega_com_motivo(servidor, navegador, monkeypatch):
    base_url, caminho_db = servidor
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=False,
        metodo=None,
        marcador_encontrado=None,
        arquivo_tr_id=None,
        inicio_pagina=None,
        inicio_localizador=None,
        motivo_nao_identificado="não achei o marcador de TR no texto deste processo",
    )
    monkeypatch.setattr("app.pipeline.identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)

    pagina = navegador.new_page()
    try:
        pagina.goto(f"{base_url}/processos/{processo_id}/checklist")

        with pagina.expect_navigation(timeout=10000):
            pagina.click("#btn-verificar-inconsistencias")

        conteudo = pagina.content()
        assert "Comparação não possível" in conteudo
        assert "não achei o marcador de TR no texto deste processo" in conteudo
    finally:
        pagina.close()
