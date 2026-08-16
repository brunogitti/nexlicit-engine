# Funções de acesso ao banco. Todo SQL do projeto mora aqui — o resto do
# código (futuras rotas do Passo 6) chama estas funções, nunca escreve SQL
# solto em outro lugar.
#
# Cada função abre sua própria conexão (via obter_conexao), faz seu trabalho
# dentro de uma transação e fecha a conexão antes de devolver o resultado —
# isso garante commit ao final ou rollback se algo falhar no meio, sem
# deixar o banco pela metade.

from datetime import datetime, timezone
from typing import Any

from app.db.conexao import obter_conexao


class RegistroNaoEncontradoError(Exception):
    """Não existe linha com o id informado para atualizar."""


def _agora_iso() -> str:
    # UTC explícito (não hora local "ingênua"): assim o horário guardado não
    # depende do fuso da máquina onde o processo Python está rodando.
    return datetime.now(timezone.utc).isoformat()


def criar_processo(dados: dict[str, Any], caminho_banco: str | None = None) -> int:
    """Cria um processo. `dados` precisa ter "nome"; os demais campos
    (orgao, modalidade, objeto, valor_estimado, data_sessao, plataforma) são
    opcionais e viram NULL se ausentes. Devolve o id criado."""
    conexao = obter_conexao(caminho_banco)
    try:
        cursor = conexao.execute(
            """
            INSERT INTO processo
                (nome, orgao, modalidade, objeto, valor_estimado, data_sessao, plataforma, criado_em)
            VALUES
                (:nome, :orgao, :modalidade, :objeto, :valor_estimado, :data_sessao, :plataforma, :criado_em)
            """,
            {
                "nome": dados["nome"],
                "orgao": dados.get("orgao"),
                "modalidade": dados.get("modalidade"),
                "objeto": dados.get("objeto"),
                "valor_estimado": dados.get("valor_estimado"),
                "data_sessao": dados.get("data_sessao"),
                "plataforma": dados.get("plataforma"),
                "criado_em": _agora_iso(),
            },
        )
        conexao.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def criar_arquivo(
    processo_id: int, dados: dict[str, Any], caminho_banco: str | None = None
) -> int:
    """Registra um arquivo (PDF/DOCX) já extraído (Passo 2), vinculado a um
    processo. `dados` precisa ter "nome_arquivo" e "tipo" ("pdf"/"docx");
    "num_paginas" e "texto_extraido" são opcionais. Devolve o id criado."""
    conexao = obter_conexao(caminho_banco)
    try:
        cursor = conexao.execute(
            """
            INSERT INTO arquivo (processo_id, nome_arquivo, tipo, num_paginas, texto_extraido)
            VALUES (:processo_id, :nome_arquivo, :tipo, :num_paginas, :texto_extraido)
            """,
            {
                "processo_id": processo_id,
                "nome_arquivo": dados["nome_arquivo"],
                "tipo": dados["tipo"],
                "num_paginas": dados.get("num_paginas"),
                "texto_extraido": dados.get("texto_extraido"),
            },
        )
        conexao.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def salvar_texto_paginas(
    processo_id: int,
    arquivo_id: int,
    lista_paginas: list[dict[str, Any]],
    caminho_banco: str | None = None,
) -> list[int]:
    """Grava em lote o texto bruto por página de UM arquivo (Fase 2, Camada
    0), numa transação só — se uma falhar, nenhuma é salva.

    Cada item de `lista_paginas` precisa ter "localizador" e "texto";
    "numero_pagina" é opcional (fica NULL pra DOCX, que não tem página real
    — ver comentário na tabela texto_pagina, em app/db/schema.sql).

    `arquivo_id` já vem resolvido de quem chama (normalmente logo depois de
    criar_arquivo, que devolve esse id) — diferente de salvar_exigencias e
    salvar_requisitos_item, não precisa resolver nome pra id aqui porque
    quem popula essa tabela é o próprio pipeline, que já tem o id em mãos
    no momento da extração.

    Devolve a lista de ids criados, na mesma ordem de entrada.
    """
    conexao = obter_conexao(caminho_banco)
    try:
        ids_criados: list[int] = []
        for pagina in lista_paginas:
            cursor = conexao.execute(
                """
                INSERT INTO texto_pagina (processo_id, arquivo_id, numero_pagina, localizador, texto)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    processo_id,
                    arquivo_id,
                    pagina.get("numero_pagina"),
                    pagina["localizador"],
                    pagina["texto"],
                ),
            )
            assert cursor.lastrowid is not None
            ids_criados.append(cursor.lastrowid)

        conexao.commit()
        return ids_criados
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def obter_texto_paginas(processo_id: int, caminho_banco: str | None = None) -> list[dict[str, Any]]:
    """Devolve o texto bruto por página de TODOS os arquivos de um processo,
    ordenado por arquivo e depois por página (Fase 2, Camada 1 — é o que
    monta o contexto do assistente de perguntas).

    Função de leitura separada de propósito, não uma chave a mais no dict de
    obter_processo(): essa tabela pode ter centenas de linhas por processo
    (uma por página do edital inteiro), e a maioria de quem chama
    obter_processo() (tela de checklist, listagem) não precisa do texto
    bruto inteiro — só quem monta a pergunta precisa.

    Devolve lista vazia se o processo não existir ou não tiver texto salvo
    ainda (não é erro aqui — quem decide se isso é um problema é
    app.pipeline.responder_pergunta_processo, que sabe o contexto de uso).
    """
    conexao = obter_conexao(caminho_banco)
    try:
        linhas = conexao.execute(
            "SELECT * FROM texto_pagina WHERE processo_id = ? ORDER BY arquivo_id, id",
            (processo_id,),
        ).fetchall()
        return [dict(linha) for linha in linhas]
    finally:
        conexao.close()


def salvar_exigencias(
    processo_id: int,
    lista_exigencias: list[dict[str, Any]],
    caminho_banco: str | None = None,
) -> list[int]:
    """Grava em lote as exigências já validadas (saída do Passo 4:
    validar_exigencias), numa transação só — se uma falhar, nenhuma é salva.

    Cada exigência traz "arquivo_origem" como o NOME do arquivo (é o que o
    validador devolve), não o id; esta função resolve o nome para o
    arquivo_origem_id correspondente, procurando entre os arquivos já
    registrados para este processo_id (por isso criar_arquivo tem que ser
    chamado antes, para cada arquivo, para que a exigência ache o vínculo).
    Se "arquivo_origem" vier preenchido mas não bater com nenhum arquivo
    registrado, é um sinal de uso incorreto da função — levanta ValueError em
    vez de gravar um vínculo errado ou silenciosamente nulo.

    Devolve a lista de ids criados, na mesma ordem de entrada.
    """
    conexao = obter_conexao(caminho_banco)
    try:
        cache_arquivo_id: dict[str, int] = {}

        def resolver_arquivo_id(nome_arquivo: str | None) -> int | None:
            if nome_arquivo is None:
                return None
            if nome_arquivo not in cache_arquivo_id:
                linha = conexao.execute(
                    "SELECT id FROM arquivo WHERE processo_id = ? AND nome_arquivo = ?",
                    (processo_id, nome_arquivo),
                ).fetchone()
                if linha is None:
                    raise ValueError(
                        f"arquivo '{nome_arquivo}' não está registrado para o "
                        f"processo {processo_id} — chame criar_arquivo antes de "
                        "salvar_exigencias"
                    )
                cache_arquivo_id[nome_arquivo] = linha["id"]
            return cache_arquivo_id[nome_arquivo]

        ids_criados: list[int] = []
        for exigencia in lista_exigencias:
            pagina = exigencia.get("pagina")
            cursor = conexao.execute(
                """
                INSERT INTO exigencia (
                    processo_id, categoria, descricao, base_legal, trecho,
                    pagina, localizador, arquivo_origem_id, obrigatorio_para,
                    confianca, cruzou_pagina, ocorrencias_encontradas,
                    grupo_hipoteses
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    processo_id,
                    exigencia["categoria"],
                    exigencia["descricao"],
                    exigencia.get("base_legal"),
                    exigencia.get("trecho"),
                    str(pagina) if pagina is not None else None,
                    exigencia.get("localizador"),
                    resolver_arquivo_id(exigencia.get("arquivo_origem")),
                    exigencia["obrigatorio_para"],
                    exigencia["confianca"],
                    int(bool(exigencia.get("cruzou_pagina", False))),
                    exigencia.get("ocorrencias_encontradas", 0),
                    exigencia.get("grupo_hipoteses"),
                ),
            )
            assert cursor.lastrowid is not None
            ids_criados.append(cursor.lastrowid)

        conexao.commit()
        return ids_criados
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def salvar_requisitos_item(
    processo_id: int,
    lista_requisitos: list[dict[str, Any]],
    caminho_banco: str | None = None,
) -> list[int]:
    """Grava em lote os requisitos técnicos por item (extração
    determinística do Passo 8: amostra + palavras-chave, já deduplicados),
    numa transação só — se um falhar, nenhum é salvo.

    Mesmo padrão de salvar_exigencias: cada requisito traz "arquivo_origem"
    como o NOME do arquivo, resolvido aqui pro arquivo_origem_id (FK) —
    procurando entre os arquivos já registrados pra este processo_id. Se
    vier preenchido mas não bater com nenhum arquivo registrado, levanta
    ValueError em vez de gravar vínculo errado ou silenciosamente nulo.

    Devolve a lista de ids criados, na mesma ordem de entrada.
    """
    conexao = obter_conexao(caminho_banco)
    try:
        cache_arquivo_id: dict[str, int] = {}

        def resolver_arquivo_id(nome_arquivo: str | None) -> int | None:
            if nome_arquivo is None:
                return None
            if nome_arquivo not in cache_arquivo_id:
                linha = conexao.execute(
                    "SELECT id FROM arquivo WHERE processo_id = ? AND nome_arquivo = ?",
                    (processo_id, nome_arquivo),
                ).fetchone()
                if linha is None:
                    raise ValueError(
                        f"arquivo '{nome_arquivo}' não está registrado para o "
                        f"processo {processo_id} — chame criar_arquivo antes de "
                        "salvar_requisitos_item"
                    )
                cache_arquivo_id[nome_arquivo] = linha["id"]
            return cache_arquivo_id[nome_arquivo]

        ids_criados: list[int] = []
        for requisito in lista_requisitos:
            cursor = conexao.execute(
                """
                INSERT INTO requisito_item (
                    processo_id, arquivo_origem_id, numero_item, categoria,
                    gatilho, trecho, pagina, localizador, ocorrencias_encontradas
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    processo_id,
                    resolver_arquivo_id(requisito.get("arquivo_origem")),
                    requisito["numero_item"],
                    requisito["categoria"],
                    requisito["gatilho"],
                    requisito["trecho"],
                    requisito.get("pagina"),
                    requisito.get("localizador"),
                    requisito.get("ocorrencias_encontradas", 1),
                ),
            )
            assert cursor.lastrowid is not None
            ids_criados.append(cursor.lastrowid)

        conexao.commit()
        return ids_criados
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def limpar_analise_do_processo(processo_id: int, caminho_banco: str | None = None) -> None:
    """Remove todas as exigências, requisitos por item, texto por página e
    arquivos de um processo (não o processo em si) — usado pelo pipeline
    (Passo 6) para reprocessar do zero. A ordem importa: exigencia,
    requisito_item e texto_pagina têm FK para arquivo, então precisam ser
    removidas primeiro (senão a FK bloqueia o DELETE do arquivo)."""
    conexao = obter_conexao(caminho_banco)
    try:
        conexao.execute("DELETE FROM exigencia WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM requisito_item WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM texto_pagina WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM arquivo WHERE processo_id = ?", (processo_id,))
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def salvar_inconsistencias(
    processo_id: int,
    lista_inconsistencias: list[dict[str, Any]],
    caminho_banco: str | None = None,
) -> list[int]:
    """Grava em lote as inconsistências edital-vs-TR encontradas pela IA
    (Fase 2, motor de inconsistências — Camada 1), numa transação só — se
    uma falhar, nenhuma é salva.

    Cada item precisa ter "tipo", "descricao", "trecho_edital",
    "pagina_edital", "trecho_tr" e "pagina_tr" — mesmo formato que
    app.ia.llm_client.detectar_inconsistencias devolve.

    Não limpa inconsistências antigas antes de salvar — isso é
    responsabilidade de quem chama (ver limpar_inconsistencias_do_processo),
    pra manter esta função um "insere lote" puro, mesmo padrão de
    salvar_exigencias e salvar_texto_paginas.

    Devolve a lista de ids criados, na mesma ordem de entrada.
    """
    conexao = obter_conexao(caminho_banco)
    try:
        ids_criados: list[int] = []
        for inconsistencia in lista_inconsistencias:
            cursor = conexao.execute(
                """
                INSERT INTO inconsistencia (
                    processo_id, tipo, descricao, trecho_edital, pagina_edital,
                    trecho_tr, pagina_tr
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    processo_id,
                    inconsistencia["tipo"],
                    inconsistencia["descricao"],
                    inconsistencia["trecho_edital"],
                    inconsistencia.get("pagina_edital"),
                    inconsistencia["trecho_tr"],
                    inconsistencia.get("pagina_tr"),
                ),
            )
            assert cursor.lastrowid is not None
            ids_criados.append(cursor.lastrowid)

        conexao.commit()
        return ids_criados
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def limpar_inconsistencias_do_processo(processo_id: int, caminho_banco: str | None = None) -> None:
    """Remove todas as inconsistências salvas de um processo — chamado antes
    de cada nova rodada de detecção (Fase 2, Camada 1), pra não acumular
    resultado de rodadas antigas junto com o novo. Sem FK apontando PARA
    "inconsistencia" (diferente de limpar_analise_do_processo), não tem
    ordem de DELETE pra se preocupar."""
    conexao = obter_conexao(caminho_banco)
    try:
        conexao.execute("DELETE FROM inconsistencia WHERE processo_id = ?", (processo_id,))
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def obter_inconsistencias(processo_id: int, caminho_banco: str | None = None) -> list[dict[str, Any]]:
    """Devolve as inconsistências já salvas de um processo, na ordem em que
    foram inseridas. Lista vazia se nunca rodou a detecção (Fase 2, Camada
    1) ou se rodou e não achou nenhuma — não é erro em nenhum dos casos."""
    conexao = obter_conexao(caminho_banco)
    try:
        linhas = conexao.execute(
            "SELECT * FROM inconsistencia WHERE processo_id = ? ORDER BY id",
            (processo_id,),
        ).fetchall()
        return [dict(linha) for linha in linhas]
    finally:
        conexao.close()


def atualizar_status_checklist(
    processo_id: int,
    sucesso: bool,
    erro: str | None,
    caminho_banco: str | None = None,
) -> None:
    """Registra o resultado da ÚLTIMA tentativa de extrair o checklist
    (Passo 3/6) nas colunas "checklist_*" de "processo" (ver comentário na
    CREATE TABLE, em schema.sql, pro porquê disso existir: sem isso, a UI
    não consegue distinguir "nunca analisado" de "analisado e deu 0
    exigências de verdade" de "tentou analisar e falhou no meio").

    Chamado nos dois casos (sucesso=True quando o checklist foi salvo,
    sucesso=False quando a extração ou o salvamento falhou) — diferente da
    Camada 3, aqui TEM que ser chamado até na falha, porque é exatamente o
    caso que este registro existe pra cobrir. Ver app.pipeline.
    processar_processo, que chama isto dentro de um except e relança a
    exceção original em seguida — o comportamento de erro pro chamador
    (HTTP 502, tela "análise falhou") não muda, só passa a ficar
    registrado no banco também."""
    conexao = obter_conexao(caminho_banco)
    try:
        conexao.execute(
            """
            UPDATE processo
            SET checklist_verificado_em = ?,
                checklist_sucesso = ?,
                checklist_erro = ?
            WHERE id = ?
            """,
            (_agora_iso(), 1 if sucesso else 0, erro, processo_id),
        )
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def atualizar_status_deteccao_inconsistencias(
    processo_id: int,
    comparacao_possivel: bool,
    motivo_impossibilidade: str | None,
    caminho_banco: str | None = None,
) -> None:
    """Registra o resultado da ÚLTIMA rodada de detecção de inconsistências
    (Fase 2, Camada 2 — UI) nas colunas "inconsistencias_*" de "processo"
    (ver comentário na CREATE TABLE, em schema.sql, pro porquê disso
    existir: sem isso, a UI não consegue distinguir "nunca rodou" de
    "rodou e não achou nada").

    Chamado sempre que app.pipeline.detectar_inconsistencias_processo roda
    até o fim, com ou sem achado — só NÃO é chamado se a função falhar antes
    disso (processo inexistente, erro de rede/IA), porque nesses casos a
    detecção não chegou a se completar de verdade."""
    conexao = obter_conexao(caminho_banco)
    try:
        conexao.execute(
            """
            UPDATE processo
            SET inconsistencias_verificado_em = ?,
                inconsistencias_comparacao_possivel = ?,
                inconsistencias_motivo_impossibilidade = ?
            WHERE id = ?
            """,
            (_agora_iso(), 1 if comparacao_possivel else 0, motivo_impossibilidade, processo_id),
        )
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def _linha_exigencia_para_dict(linha: Any) -> dict[str, Any]:
    dados = dict(linha)
    dados["cruzou_pagina"] = bool(dados["cruzou_pagina"])
    return dados


def obter_processo(id: int, caminho_banco: str | None = None) -> dict[str, Any] | None:
    """Devolve o processo com seus arquivos, exigências e requisitos por
    item montados, ou None se o id não existir.

    Usa quatro queries simples (processo, arquivos, exigências, requisitos
    por item) em vez de JOINs: um JOIN entre as tabelas duplicaria cada
    exigência/requisito uma vez por arquivo do processo (produto cruzado),
    exigindo desduplicar na volta — mais complexo que só rodar SELECTs
    separados e montar o dict em Python. Como um processo tem no máximo
    alguns arquivos e algumas dezenas/centenas de linhas nas outras tabelas,
    o custo de queries a mais é irrelevante.
    """
    conexao = obter_conexao(caminho_banco)
    try:
        linha_processo = conexao.execute(
            "SELECT * FROM processo WHERE id = ?", (id,)
        ).fetchone()
        if linha_processo is None:
            return None

        linhas_arquivos = conexao.execute(
            "SELECT * FROM arquivo WHERE processo_id = ? ORDER BY id", (id,)
        ).fetchall()
        linhas_exigencias = conexao.execute(
            "SELECT * FROM exigencia WHERE processo_id = ? ORDER BY id", (id,)
        ).fetchall()
        linhas_requisitos_item = conexao.execute(
            "SELECT * FROM requisito_item WHERE processo_id = ? ORDER BY numero_item, id",
            (id,),
        ).fetchall()

        return {
            **dict(linha_processo),
            "arquivos": [dict(linha) for linha in linhas_arquivos],
            "exigencias": [_linha_exigencia_para_dict(linha) for linha in linhas_exigencias],
            "requisitos_item": [dict(linha) for linha in linhas_requisitos_item],
        }
    finally:
        conexao.close()


def listar_processos(caminho_banco: str | None = None) -> list[dict[str, Any]]:
    """Histórico de processos, mais recente primeiro (para a futura rota
    GET /processos). Não traz arquivos/exigências — só os dados do processo;
    quem quiser o detalhe completo chama obter_processo(id) depois."""
    conexao = obter_conexao(caminho_banco)
    try:
        linhas = conexao.execute(
            "SELECT * FROM processo ORDER BY criado_em DESC, id DESC"
        ).fetchall()
        return [dict(linha) for linha in linhas]
    finally:
        conexao.close()


def excluir_processo(processo_id: int, caminho_banco: str | None = None) -> None:
    """Remove um processo e TODAS as linhas relacionadas — exclusão
    definitiva (diferente de limpar_analise_do_processo, que só limpa a
    análise pra reprocessar, mantendo o processo em si).

    Usada pela limpeza de processos antigos (app/limpeza.py, 16/08/2026).

    A ordem do DELETE importa: nenhuma FK do schema.sql tem ON DELETE
    CASCADE (só REFERENCES simples) e PRAGMA foreign_keys=ON está ativo em
    toda conexão (app/db/conexao.py) — por isso cada tabela filha precisa
    ser esvaziada manualmente antes do DELETE do processo, na ordem certa:
    texto_pagina e exigencia referenciam tanto processo_id quanto
    arquivo_id/arquivo_origem_id, então saem antes de arquivo; requisito_item
    também referencia arquivo_origem_id (nullable, mas a ordem não custa
    nada de qualquer forma); inconsistencia só referencia processo_id.
    Só depois de todas essas é seguro apagar "arquivo", e só depois de
    "arquivo" é seguro apagar o "processo".

    NÃO toca a tabela "empresa" de forma nenhuma — não existe FK de
    processo para empresa no schema (cadastro de fornecedor é
    independente por natureza), então não há cascade acidental possível
    aqui, nem por engano futuro: esta função nunca referencia "empresa".
    """
    conexao = obter_conexao(caminho_banco)
    try:
        conexao.execute("DELETE FROM texto_pagina WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM exigencia WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM requisito_item WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM inconsistencia WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM arquivo WHERE processo_id = ?", (processo_id,))
        conexao.execute("DELETE FROM processo WHERE id = ?", (processo_id,))
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def atualizar_status_exigencia(
    id: int,
    novo_status: str,
    observacao: str | None = None,
    caminho_banco: str | None = None,
) -> dict[str, Any]:
    """Atualiza o status_check de uma exigência (para o clique do checkbox,
    Passo 6/7) e devolve o registro já atualizado — a rota PATCH usa isso
    direto na resposta, sem precisar de uma segunda consulta.

    Se `observacao` não for passada (None), o texto já salvo antes fica como
    está — só troca o status; passar uma string sobrescreve a observação
    também. Isso evita que marcar um checkbox sem digitar nada apague uma
    observação que o usuário já tinha escrito antes.

    Levanta RegistroNaoEncontradoError se o id não existir (em vez de
    silenciosamente não fazer nada, que é o comportamento padrão de um
    UPDATE do SQLite sem WHERE que bate).
    """
    conexao = obter_conexao(caminho_banco)
    try:
        if observacao is None:
            cursor = conexao.execute(
                "UPDATE exigencia SET status_check = ? WHERE id = ?",
                (novo_status, id),
            )
        else:
            cursor = conexao.execute(
                "UPDATE exigencia SET status_check = ?, observacao_usuario = ? WHERE id = ?",
                (novo_status, observacao, id),
            )

        if cursor.rowcount == 0:
            raise RegistroNaoEncontradoError(f"exigência {id} não existe")

        linha = conexao.execute("SELECT * FROM exigencia WHERE id = ?", (id,)).fetchone()
        conexao.commit()
        return _linha_exigencia_para_dict(linha)
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


# ---------- Fase 4, Camada 0: cadastro de empresas fornecedoras ----------


_CAMPOS_EMPRESA = (
    "razao_social", "nome_fantasia", "cnpj", "endereco",
    "representante_legal_nome", "representante_legal_cpf", "representante_legal_cargo",
    "telefone", "email", "regime_tributario",
)


def criar_empresa(dados: dict[str, Any], caminho_banco: str | None = None) -> int:
    """Cadastra uma empresa fornecedora. `dados` precisa ter "razao_social"
    e "cnpj"; os demais campos de _CAMPOS_EMPRESA são opcionais e viram
    NULL se ausentes. Devolve o id criado."""
    conexao = obter_conexao(caminho_banco)
    try:
        cursor = conexao.execute(
            f"""
            INSERT INTO empresa ({", ".join(_CAMPOS_EMPRESA)}, criado_em)
            VALUES ({", ".join(f":{campo}" for campo in _CAMPOS_EMPRESA)}, :criado_em)
            """,
            {**{campo: dados.get(campo) for campo in _CAMPOS_EMPRESA}, "criado_em": _agora_iso()},
        )
        # "razao_social" e "cnpj" são NOT NULL no schema — se vierem
        # ausentes de `dados`, o INSERT falha com sqlite3.IntegrityError
        # (já tratado por app/erros.py -> 400), não silenciosamente como
        # NULL. dados.get() aqui não esconde isso: só evita KeyError pros
        # campos opcionais.
        conexao.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def obter_empresa(id: int, caminho_banco: str | None = None) -> dict[str, Any] | None:
    """Devolve a empresa com esse id, ou None se não existir."""
    conexao = obter_conexao(caminho_banco)
    try:
        linha = conexao.execute("SELECT * FROM empresa WHERE id = ?", (id,)).fetchone()
        return dict(linha) if linha is not None else None
    finally:
        conexao.close()


def listar_empresas(caminho_banco: str | None = None) -> list[dict[str, Any]]:
    """Todas as empresas cadastradas, ordenadas por razão social — lista de
    seleção pequena (o Bruno atende um punhado de clientes, não centenas),
    ordem alfabética é mais fácil de escanear que "mais recente primeiro"
    (que faz sentido pra processo, não faz muito aqui)."""
    conexao = obter_conexao(caminho_banco)
    try:
        linhas = conexao.execute("SELECT * FROM empresa ORDER BY razao_social").fetchall()
        return [dict(linha) for linha in linhas]
    finally:
        conexao.close()


def atualizar_empresa(id: int, dados: dict[str, Any], caminho_banco: str | None = None) -> dict[str, Any]:
    """Atualiza os campos de uma empresa já cadastrada (tela de edição) e
    devolve o registro atualizado. Sobrescreve TODOS os campos de
    _CAMPOS_EMPRESA com o que vier em `dados` (mesmo os ausentes viram
    NULL) — é uma reescrita completa do formulário, não uma atualização
    parcial tipo PATCH de exigencia.status_check.

    Levanta RegistroNaoEncontradoError se o id não existir."""
    conexao = obter_conexao(caminho_banco)
    try:
        atribuicoes = ", ".join(f"{campo} = :{campo}" for campo in _CAMPOS_EMPRESA)
        cursor = conexao.execute(
            f"UPDATE empresa SET {atribuicoes} WHERE id = :id",
            {**{campo: dados.get(campo) for campo in _CAMPOS_EMPRESA}, "id": id},
        )
        if cursor.rowcount == 0:
            raise RegistroNaoEncontradoError(f"empresa {id} não existe")

        linha = conexao.execute("SELECT * FROM empresa WHERE id = ?", (id,)).fetchone()
        conexao.commit()
        return dict(linha)
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()
