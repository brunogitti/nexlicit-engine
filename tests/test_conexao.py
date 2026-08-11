# Testes da trava estrutural em app/db/conexao.py — reação ao incidente da
# Camada 2 (Fase 2, motor de inconsistências): um script de verificação sem
# "caminho_banco" explícito migrou o schema do banco real por efeito
# colateral (sem perda de dado, só por sorte — nenhuma proteção existia).
#
# obter_conexao() agora recusa abrir o caminho padrão (DATABASE_PATH) sem
# um sinal explícito de autorização (NEXLICIT_USE_BANCO_REAL) — a menos
# que quem chamou já tenha isolado o banco (caminho_banco explícito, ou
# monkeypatch em app.db.conexao.DATABASE_PATH, o mesmo padrão que o resto
# da suíte já usa pra testar rota HTTP).

from pathlib import Path

import pytest

from app.db.conexao import ConexaoBancoRealBloqueadaError, obter_conexao


def test_obter_conexao_sem_caminho_bloqueia_por_padrao():
    # Cenário exato do incidente: sem caminho_banco, sem monkeypatch em
    # DATABASE_PATH, sem NEXLICIT_USE_BANCO_REAL setado no ambiente (não é
    # setado por padrão — ver .env, removido de lá de propósito porque
    # .env é carregado por qualquer script rodado na pasta do projeto).
    # Nenhuma fixture de isolamento aqui de propósito: é o comportamento
    # cru da função, exatamente o que um script avulso encontraria.
    with pytest.raises(ConexaoBancoRealBloqueadaError, match="NEXLICIT_USE_BANCO_REAL"):
        obter_conexao()


def test_obter_conexao_bloqueia_quando_caminho_resolvido_bate_com_configurado(tmp_path, monkeypatch):
    # Mesma checagem do teste acima, mas com um caminho FAKE (não o
    # nexlicit.db de verdade) — prova que a trava reage à SITUAÇÃO (caminho
    # resolvido == configurado, sem isolar) e não é uma checagem hardcoded
    # só pro nome "nexlicit.db". Nada é criado no disco nesse caminho fake,
    # porque a exceção precisa disparar ANTES de sqlite3.connect().
    caminho_fake_producao = str(tmp_path / "fake_producao.db")
    monkeypatch.setattr("app.config.DATABASE_PATH", caminho_fake_producao)
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_fake_producao)
    monkeypatch.setattr("app.db.conexao.USAR_BANCO_REAL", False)

    with pytest.raises(ConexaoBancoRealBloqueadaError):
        obter_conexao()

    assert not Path(caminho_fake_producao).exists()


def test_obter_conexao_permite_banco_configurado_com_flag_explicita(tmp_path, monkeypatch):
    # Com NEXLICIT_USE_BANCO_REAL explicitamente ligado (simulando o
    # servidor de verdade, iniciado com a variável de ambiente setada na
    # hora), a conexão no caminho configurado funciona normalmente.
    caminho_fake_producao = str(tmp_path / "fake_producao.db")
    monkeypatch.setattr("app.config.DATABASE_PATH", caminho_fake_producao)
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_fake_producao)
    monkeypatch.setattr("app.db.conexao.USAR_BANCO_REAL", True)

    conexao = obter_conexao()
    conexao.close()

    assert Path(caminho_fake_producao).exists()


def test_obter_conexao_com_caminho_banco_explicito_nunca_bloqueia(tmp_path, monkeypatch):
    # Padrão normal dos testes do resto da suíte: caminho_banco explícito
    # sempre funciona, independente da flag ou do DATABASE_PATH configurado
    # — é exatamente esse isolamento explícito que a trava reconhece como
    # seguro.
    monkeypatch.setattr("app.db.conexao.USAR_BANCO_REAL", False)
    caminho_isolado = str(tmp_path / "isolado.db")

    conexao = obter_conexao(caminho_isolado)
    conexao.close()

    assert Path(caminho_isolado).exists()


def test_obter_conexao_com_database_path_isolado_via_monkeypatch_nao_bloqueia(tmp_path, monkeypatch):
    # Padrão usado pelos testes de rota HTTP (test_rotas.py, test_paginas.py
    # etc.): a rota não recebe caminho_banco, então o isolamento é feito
    # via monkeypatch direto em app.db.conexao.DATABASE_PATH. Como o valor
    # monkeypatched diverge do app.config.DATABASE_PATH original, a trava
    # reconhece que já houve isolamento explícito e não bloqueia — sem
    # precisar de NEXLICIT_USE_BANCO_REAL nenhum.
    caminho_isolado = str(tmp_path / "isolado_via_monkeypatch.db")
    monkeypatch.setattr("app.db.conexao.DATABASE_PATH", caminho_isolado)
    monkeypatch.setattr("app.db.conexao.USAR_BANCO_REAL", False)

    conexao = obter_conexao()
    conexao.close()

    assert Path(caminho_isolado).exists()
