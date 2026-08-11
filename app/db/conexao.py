# Abre a conexão com o banco SQLite e garante que o schema (as tabelas)
# existe. É o único lugar do projeto que chama sqlite3.connect() — quem
# precisar do banco usa obter_conexao(), nunca abre uma conexão por conta
# própria (assim PRAGMA foreign_keys e o schema ficam garantidos sempre).

import sqlite3
from pathlib import Path

import app.config as _config
from app.config import DATABASE_PATH, USAR_BANCO_REAL

_CAMINHO_SCHEMA = Path(__file__).parent / "schema.sql"


class ConexaoBancoRealBloqueadaError(Exception):
    """obter_conexao() foi chamada sem "caminho_banco" explícito, e o
    caminho resolvido é o banco real configurado no .env (DATABASE_PATH),
    sem NEXLICIT_USE_BANCO_REAL=1 setado no ambiente.

    Trava estrutural, não só disciplina: um script de verificação/depuração
    sem "caminho_banco" explícito já migrou o schema do banco real por
    engano (Fase 2, Camada 2 — 2 tabelas vazias criadas por efeito
    colateral, sem perda de dado só por sorte). O servidor de verdade
    (uvicorn app.main:app) tem NEXLICIT_USE_BANCO_REAL=1 no .env, então
    continua funcionando normal — só quem esquece de isolar o banco (ex.:
    um "python -c" avulso) cai aqui.
    """


def obter_conexao(caminho_banco: str | None = None) -> sqlite3.Connection:
    """Abre uma conexão com o banco SQLite e aplica o schema.

    `caminho_banco` é opcional: por padrão usa DATABASE_PATH (do .env,
    Passo 1). Os testes passam um caminho de arquivo temporário aqui pra
    isolar cada teste num banco próprio, sem mexer no banco real — ou
    fazem monkeypatch em app.db.conexao.DATABASE_PATH (padrão já usado
    pelos testes de rota, que não têm como passar caminho_banco porque a
    rota HTTP de verdade não recebe esse parâmetro).

    Quando "caminho_banco" vem None E o caminho resolvido é exatamente o
    DATABASE_PATH original do .env (ninguém fez monkeypatch nele pra
    isolar) E NEXLICIT_USE_BANCO_REAL não está setado, levanta
    ConexaoBancoRealBloqueadaError em vez de conectar — ver o docstring da
    exceção pro porquê. Comparar contra `_config.DATABASE_PATH` (o módulo,
    não o nome importado) de propósito: isolar um teste faz monkeypatch em
    `app.db.conexao.DATABASE_PATH` (o nome importado aqui embaixo), que é
    justamente o que compara contra o valor original — se alguém isolou o
    banco, os dois valores divergem e a trava não pega.
    """
    if caminho_banco is not None:
        caminho = caminho_banco
    elif DATABASE_PATH == _config.DATABASE_PATH and not USAR_BANCO_REAL:
        raise ConexaoBancoRealBloqueadaError(
            f"obter_conexao() foi chamada sem caminho_banco explícito, e o "
            f"caminho resolvido ({DATABASE_PATH!r}) é o banco real do .env. "
            "Se isto é um script de verificação/teste, passe caminho_banco "
            "explícito (um arquivo em diretório temporário). Se a intenção "
            "é mesmo usar o banco real, defina NEXLICIT_USE_BANCO_REAL=1 "
            "no ambiente."
        )
    else:
        caminho = DATABASE_PATH

    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row

    # Ativação por conexão: precisa rodar toda vez, o SQLite não guarda essa
    # configuração junto com o banco.
    conexao.execute("PRAGMA foreign_keys = ON")

    # Idempotente (CREATE TABLE IF NOT EXISTS): seguro rodar em toda conexão.
    conexao.executescript(_CAMINHO_SCHEMA.read_text(encoding="utf-8"))

    # CREATE TABLE IF NOT EXISTS não adiciona coluna nova a uma tabela que já
    # existia num banco criado antes dessa coluna entrar no schema.sql (ex.:
    # nexlicit.db já tinha processos reais analisados antes de "exigencia"
    # ganhar "grupo_hipoteses"). Migração aditiva, sem apagar nada.
    _garantir_colunas_novas(conexao)

    return conexao


# Toda coluna adicionada a uma tabela já existente depois do lançamento
# entra aqui — (tabela, coluna, definição SQL da coluna). CREATE TABLE
# resolve tabela nova; isso aqui resolve coluna nova em tabela já existente.
_COLUNAS_ADITIVAS = [
    ("exigencia", "grupo_hipoteses", "TEXT"),
    # Fase 2 (motor de inconsistências), Camada 2 — ver comentário na
    # CREATE TABLE processo, em schema.sql, pro que cada uma significa.
    ("processo", "inconsistencias_verificado_em", "TEXT"),
    ("processo", "inconsistencias_comparacao_possivel", "INTEGER"),
    ("processo", "inconsistencias_motivo_impossibilidade", "TEXT"),
]


def _garantir_colunas_novas(conexao: sqlite3.Connection) -> None:
    for tabela, coluna, definicao in _COLUNAS_ADITIVAS:
        colunas_existentes = {
            linha["name"] for linha in conexao.execute(f"PRAGMA table_info({tabela})")
        }
        if coluna not in colunas_existentes:
            conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
