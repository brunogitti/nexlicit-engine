# Orquestração: costura extrator (Passo 2) + llm_client (Passo 3) +
# validador (Passo 4) + repositorio (Passo 5) num fluxo só. Testável sem
# FastAPI — a rota (Passo 6, app/rotas/) só chama processar_processo() e
# decide como virar resposta HTTP a partir das exceções que ela levanta.

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.db.repositorio import (
    atualizar_status_checklist,
    atualizar_status_deteccao_inconsistencias,
    criar_arquivo,
    limpar_analise_do_processo,
    limpar_inconsistencias_do_processo,
    obter_processo,
    obter_texto_paginas,
    salvar_exigencias,
    salvar_inconsistencias,
    salvar_requisitos_item,
    salvar_texto_paginas,
)
from app.extracao.exigencias_item import (
    carregar_palavras_chave,
    deduplicar_exigencias_item,
    extrair_exigencias_por_item,
)
from app.extracao.extrator import DocumentoExtraido, extrair_texto
from app.extracao.tabela_itens import localizar_documento_com_tabela, normalizar_com_mapa
from app.ia.llm_client import extrair_checklist
from app.ia.llm_client import detectar_inconsistencias as _detectar_inconsistencias_ia
from app.ia.llm_client import responder_pergunta as _responder_pergunta_ia
from app.ia.llm_client import contar_tokens_comparacao as _contar_tokens_comparacao_ia
from app.ia.llm_client import LIMITE_TOKENS_POR_MINUTO
from app.inconsistencias.limite_tr import identificar_blocos_edital_tr
from app.validacao.validador import validar_exigencias

logger = logging.getLogger(__name__)


class ProcessoNaoEncontradoError(Exception):
    """processo_id não existe no banco."""


class ProcessoJaAnalisadoError(Exception):
    """O processo já tem arquivos/exigências salvos de uma análise anterior."""


class ProcessoSemArquivosError(Exception):
    """Não há nenhum arquivo pra processar (nada foi enviado pra esse processo)."""


class ProcessoSemTextoExtraidoError(Exception):
    """O processo existe, mas não tem texto por página salvo (Fase 2, Camada
    0) — ainda não foi analisado (POST /processos/{id}/analisar), ou foi
    analisado antes da Camada 0 existir e precisa ser reprocessado."""


def _texto_do_documento(documento: DocumentoExtraido) -> str:
    return "\n\n".join(bloco.texto for bloco in documento.blocos)


def processar_processo(
    processo_id: int,
    caminhos_arquivos: list[str],
    forcar_reprocessamento: bool = False,
    caminho_banco: str | None = None,
) -> dict[str, Any]:
    """Roda o pipeline inteiro para um processo já existente:

    1. Extrai o texto de cada arquivo (Passo 2) e registra o arquivo no
       banco (criar_arquivo), junto com o texto bruto por página (Fase 2,
       Camada 0 — pro assistente de perguntas da Camada 1 citar de onde
       tirou cada resposta).
    2. Concatena o texto de todos os arquivos e manda pra IA extrair o
       checklist (Passo 3) — o modelo não sabe de qual arquivo veio cada
       trecho, isso quem descobre depois é o validador.
    3. Valida cada trecho contra o texto-fonte, arquivo por arquivo (Passo
       4), resolvendo confiança/página/arquivo de origem.
    4. Salva tudo no banco, vinculado ao processo (Passo 5).
    5. Extrai requisitos técnicos por item (amostra + palavras-chave, Passo
       8) do arquivo que tiver uma tabela de itens reconhecível — etapa
       determinística, sem IA e sem o validador do Passo 4 (o trecho já sai
       literal do texto-fonte), roda independente do resultado da IA.

    Devolve um resumo: {"processo_id", "arquivos_processados",
    "total_exigencias", "localizadas", "inferidas", "requisitos_por_item"}.

    Levanta exceção clara em cada etapa que falhar — formato de arquivo não
    suportado (ValueError, do extrator), provedor de IA não configurado
    (ProvedorNaoSuportadoError) ou falha/JSON malformado da API
    (RespostaIAError), ambas do llm_client. Não formata resposta HTTP aqui:
    isso é responsabilidade de quem chama (a rota).
    """
    processo = obter_processo(processo_id, caminho_banco=caminho_banco)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {processo_id} não existe")

    if not caminhos_arquivos:
        raise ProcessoSemArquivosError(
            f"processo {processo_id} não tem arquivos enviados para analisar"
        )

    ja_analisado = bool(processo["arquivos"])
    if ja_analisado and not forcar_reprocessamento:
        raise ProcessoJaAnalisadoError(
            f"processo {processo_id} já foi analisado "
            f"({len(processo['arquivos'])} arquivo(s), "
            f"{len(processo['exigencias'])} exigência(s)). "
            "Chame de novo com forcar_reprocessamento=True para refazer do zero."
        )

    if ja_analisado and forcar_reprocessamento:
        limpar_analise_do_processo(processo_id, caminho_banco=caminho_banco)

    documentos: list[DocumentoExtraido] = []
    for caminho in caminhos_arquivos:
        try:
            documento = extrair_texto(caminho)
        except ValueError as erro:
            raise ValueError(f"arquivo '{caminho}': {erro}") from erro

        arquivo_id = criar_arquivo(
            processo_id,
            {
                "nome_arquivo": documento.nome_arquivo,
                "tipo": documento.tipo,
                "num_paginas": documento.num_paginas,
                "texto_extraido": _texto_do_documento(documento),
            },
            caminho_banco=caminho_banco,
        )

        # Fase 2, Camada 0 — texto bruto por página, pro assistente de
        # perguntas (Camada 1). Reaproveita os blocos que o extrator (Passo
        # 2) já leu página por página — não lê o arquivo de novo.
        salvar_texto_paginas(
            processo_id,
            arquivo_id,
            [
                {
                    "numero_pagina": bloco.pagina,
                    "localizador": bloco.localizador,
                    "texto": bloco.texto,
                }
                for bloco in documento.blocos
            ],
            caminho_banco=caminho_banco,
        )

        documentos.append(documento)

    texto_completo = "\n\n".join(_texto_do_documento(documento) for documento in documentos)

    contexto_processo = {
        "orgao": processo.get("orgao"),
        "modalidade": processo.get("modalidade"),
        "objeto": processo.get("objeto"),
    }

    # Registra sucesso/falha da extração do checklist (colunas
    # "checklist_*" de "processo", ver comentário na CREATE TABLE em
    # schema.sql) — sem isso, a listagem não consegue diferenciar "nunca
    # analisado" de "tentou e falhou no meio" (achado no levantamento
    # visual do dashboard, 13/08/2026). Na falha, registra e RELANÇA a
    # mesma exceção original — o comportamento pro chamador (rota HTTP
    # devolve 502 via app/erros.py, tela de espera mostra "análise
    # falhou") não muda, só passa a ficar registrado no banco também.
    try:
        exigencias_extraidas = extrair_checklist(texto_completo, contexto_processo)
        exigencias_validadas = validar_exigencias(exigencias_extraidas, documentos)
        salvar_exigencias(processo_id, exigencias_validadas, caminho_banco=caminho_banco)
    except Exception as erro:
        atualizar_status_checklist(
            processo_id, sucesso=False, erro=str(erro), caminho_banco=caminho_banco
        )
        raise
    atualizar_status_checklist(processo_id, sucesso=True, erro=None, caminho_banco=caminho_banco)

    # Requisitos técnicos por item (Passo 8) — determinístico, sem IA e sem
    # validador (o trecho já sai literal do texto-fonte, por construção).
    # Só entra se algum dos arquivos enviados tiver uma tabela de itens
    # reconhecível; se nenhum tiver, não é erro, só não há o que extrair.
    total_requisitos_item = 0
    resultado_tabela = localizar_documento_com_tabela(documentos)
    if resultado_tabela is not None:
        documento_com_tabela, itens_tabela = resultado_tabela
        palavras_chave_item = carregar_palavras_chave()
        requisitos_brutos = extrair_exigencias_por_item(itens_tabela, palavras_chave_item)
        requisitos_dedup = deduplicar_exigencias_item(requisitos_brutos)
        for requisito in requisitos_dedup:
            requisito["arquivo_origem"] = documento_com_tabela.nome_arquivo
        salvar_requisitos_item(processo_id, requisitos_dedup, caminho_banco=caminho_banco)
        total_requisitos_item = len(requisitos_dedup)

    # Fase 2, Camada 3: motor de inconsistências edital-vs-TR, automático.
    # Roda por último, depois de texto_pagina estar salvo (a Camada 0 do
    # motor precisa dele). Não deixa uma falha aqui derrubar a análise
    # inteira: o checklist (a entrega principal) já foi salvo nesse ponto
    # — se a comparação falhar (rede, IA fora do ar, contexto grande
    # demais...), o processo fica "ainda não verificado" e dá pra rodar à
    # mão depois (botão "Verificar inconsistências" na tela do checklist),
    # em vez de fazer o usuário perder a análise inteira por causa de uma
    # camada secundária.
    try:
        detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_banco)
    except Exception:
        logger.exception(
            "falha ao detectar inconsistências automaticamente para o processo "
            "%s — checklist já foi salvo normalmente, comparação pode ser "
            "refeita à mão pela tela",
            processo_id,
        )

    total = len(exigencias_validadas)
    localizadas = sum(1 for e in exigencias_validadas if e["confianca"] == "localizado")

    return {
        "processo_id": processo_id,
        "arquivos_processados": len(documentos),
        "total_exigencias": total,
        "localizadas": localizadas,
        "inferidas": total - localizadas,
        "requisitos_por_item": total_requisitos_item,
    }


def _montar_texto_marcado_por_pagina(paginas: list[dict[str, Any]]) -> str:
    """Concatena o texto de várias páginas (linhas de texto_pagina, já na
    ordem certa — quem ordena é obter_texto_paginas), cada uma precedida por
    um marcador que app.ia.llm_client usa pra citar a fonte na resposta:
    "[PÁGINA N]" quando a página é real (PDF), ou o localizador em si (ex.
    "[PARÁGRAFO 5]") quando não é (DOCX não tem página de verdade — ver
    comentário na tabela texto_pagina, em app/db/schema.sql)."""
    partes = []
    for pagina in paginas:
        if pagina["numero_pagina"] is not None:
            marcador = f"[PÁGINA {pagina['numero_pagina']}]"
        else:
            marcador = f"[{pagina['localizador'].upper()}]"
        partes.append(f"{marcador}\n{pagina['texto']}")
    return "\n\n".join(partes)


def responder_pergunta_processo(
    processo_id: int, pergunta: str, caminho_banco: str | None = None
) -> dict[str, Any]:
    """Fase 2, Camada 1: responde uma pergunta em linguagem natural sobre um
    processo já analisado, com base no texto bruto salvo por página
    (Camada 0) — monta o contexto marcado por página e chama a IA
    (app.ia.llm_client.responder_pergunta).

    Devolve {"encontrado": bool, "resposta": str, "paginas": list[int]} —
    mesmo formato que llm_client.responder_pergunta devolve, sem alterar
    nada (esta função só monta o contexto, não interpreta a resposta).

    Levanta ProcessoNaoEncontradoError se o processo não existir, ou
    ProcessoSemTextoExtraidoError se existir mas ainda não tiver texto
    salvo (precisa rodar a análise antes). Repassa ContextoGrandeDemaisError
    do llm_client sem alterar, se o texto for grande demais pro modelo.
    """
    processo = obter_processo(processo_id, caminho_banco=caminho_banco)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {processo_id} não existe")

    paginas = obter_texto_paginas(processo_id, caminho_banco=caminho_banco)
    if not paginas:
        raise ProcessoSemTextoExtraidoError(
            f"processo {processo_id} ainda não tem texto extraído — rode "
            f"POST /processos/{processo_id}/analisar antes de perguntar"
        )

    texto_marcado = _montar_texto_marcado_por_pagina(paginas)
    return _responder_pergunta_ia(texto_marcado, pergunta)


# Mesmo padrão de normalização usado pra deduplicar trecho de requisito por
# item (app/rotas/paginas.py, _chave_dedup_trecho) — mesmo espírito aqui:
# "é o mesmo texto", ignorando acento/caixa/espaço e pontuação decorativa
# que a IA pode variar entre duas cópias do mesmo trecho. Não importa de lá
# de propósito (rotas não deveria ser dependência de pipeline) — regex
# pequena e autocontida, duplicar é mais simples que reorganizar módulo.
_PADRAO_PONTUACAO_DECORATIVA = re.compile(r"[\"'’‘“”` *]")
_PADRAO_ESPACOS_MULTIPLOS = re.compile(r"\s+")


def _chave_normalizada_trecho(trecho: str) -> str:
    normalizado = normalizar_com_mapa(trecho)[0]
    sem_decoracao = _PADRAO_PONTUACAO_DECORATIVA.sub("", normalizado)
    colapsado = _PADRAO_ESPACOS_MULTIPLOS.sub(" ", sem_decoracao).strip()
    return colapsado.rstrip(".")


def _mesclar_inconsistencias(
    execucao_1: list[dict[str, Any]], execucao_2: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fase 2, Camada 3: une os achados de duas rodadas de comparação
    edital-vs-TR contra o MESMO texto — mitigação de um não-determinismo já
    confirmado na prática (golden test da Camada 1: o mesmo achado forte do
    Ouroeste sumiu numa rodada e voltou em duas outras rodadas seguintes).

    É uma UNIÃO, não uma interseção: achado que aparece nas DUAS execuções
    (mesmo tipo + trechos "iguais", ver _chave_normalizada_trecho) entra uma
    vez só; achado que aparece em SÓ UMA das duas entra do mesmo jeito —
    descartar um achado por ter aparecido numa rodada só reintroduziria o
    mesmo risco de não-determinismo que a execução dupla existe pra
    mitigar (um achado real pode não se repetir em toda rodada; isso não o
    torna falso)."""
    vistos: set[tuple[str, str, str]] = set()
    mesclado: list[dict[str, Any]] = []
    for achado in execucao_1 + execucao_2:
        chave = (
            achado["tipo"],
            _chave_normalizada_trecho(achado["trecho_edital"]),
            _chave_normalizada_trecho(achado["trecho_tr"]),
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        mesclado.append(achado)
    return mesclado


# Fase 2, Camada 3 (espaçamento): colchão de segurança abaixo do limite real
# por minuto do free tier (LIMITE_TOKENS_POR_MINUTO = 250_000, confirmado ao
# vivo via 429 real — ver comentário na constante em app/ia/llm_client.py).
# Sem colchão, uma medição de tokens ligeiramente otimista (a IA conta o
# prompt completo, incluindo instruções de sistema, então a soma real pode
# variar um pouco por chamada) já bateria o teto. 30_000 foi escolhido por
# ficar bem abaixo da folga real observada (Ouroeste passou com 207_640,
# quase 43 mil de folga; Frutal falhou com 433_378) — não é um valor exato
# calculado, é uma margem confortável dentro dessa folga observada.
_MARGEM_SEGURANCA_TOKENS_POR_MINUTO = 30_000

# Quanto esperar quando o espaçamento é necessário. Baseado no retryDelay
# real devolvido pelo próprio erro 429 do golden test do Frutal (~56.8s) —
# 60s dá uma margem confortável pra garantir que a 2ª chamada caia numa
# janela de cota nova, sem precisar reagir ao conteúdo do erro (isso é
# prevenção, não retry: a ideia é nunca deixar a 2ª chamada bater no 429).
_ESPERA_ENTRE_EXECUCOES_SEGUNDOS = 60


def _tempo_de_espera_entre_execucoes(tokens_por_chamada: int) -> int:
    """Decide se vale esperar entre a 1ª e a 2ª chamada da execução dupla
    (Camada 3), a partir do tamanho medido de UMA chamada — não um valor
    fixo igual pra qualquer edital.

    As duas chamadas comparam o MESMO texto, então usam praticamente o
    mesmo tanto de tokens de entrada cada uma; se a SOMA das duas
    ultrapassa o limite por minuto (com a margem de segurança), esperar
    entre elas dá tempo de cruzar pra uma nova janela de cota de 60s antes
    da 2ª chamada — sem isso, a 2ª chamada de um edital grande (ex.:
    Frutal) bate 429 mesmo a 1ª tendo funcionado.

    Pra edital pequeno/médio (ex.: Paulínia, Ouroeste), a soma fica bem
    abaixo do limite — devolve 0 e a análise continua tão rápida quanto
    antes, sem espera desnecessária.
    """
    limite_seguro = LIMITE_TOKENS_POR_MINUTO - _MARGEM_SEGURANCA_TOKENS_POR_MINUTO
    if tokens_por_chamada * 2 > limite_seguro:
        return _ESPERA_ENTRE_EXECUCOES_SEGUNDOS
    return 0


def detectar_inconsistencias_processo(
    processo_id: int, caminho_banco: str | None = None
) -> dict[str, Any]:
    """Motor de inconsistências edital-vs-TR (Fase 2): compara o corpo do
    edital com o Termo de Referência embutido, buscando contradições reais
    (quantidade, valor, prazo, especificação técnica, administrativo).

    Reaproveita a Camada 0 (app.inconsistencias.identificar_blocos_edital_tr)
    pra saber onde cortar o texto entre os dois blocos. Se a Camada 0 não
    conseguir identificar esse limite pra este processo, NÃO tenta comparar
    — devolve comparacao_possivel=False com o motivo, em vez de adivinhar
    um limite (mesmo princípio anti-alucinação do resto do sistema: sem
    base confiável dos dois lados, não força um palpite).

    Camada 3: quando a comparação É possível, roda a chamada à IA DUAS
    vezes e mescla os achados (ver _mesclar_inconsistencias) — custo de 2
    chamadas por processo, decisão confirmada e registrada em
    planejamento-nexlicit-engine.md, depois de confirmar o
    não-determinismo na prática (Camada 1). Usada tanto pela rota manual
    (POST /processos/{id}/detectar-inconsistencias) quanto pela análise
    automática (app.pipeline.processar_processo) — mesmo comportamento
    robusto nos dois casos, não um caminho mais "barato" e mais frágil só
    pro botão manual.

    Espaçamento entre as 2 chamadas (Camada 3, refinamento): antes de
    chamar a IA, mede via count_tokens (chamada barata, fora da cota de
    tokens/minuto que está sendo protegida) quanto UMA comparação usaria
    de tokens de entrada. Se a soma das 2 chamadas passar perto do limite
    por minuto do free tier (ver _tempo_de_espera_entre_execucoes), espera
    um pouco entre a 1ª e a 2ª — o suficiente pra editais grandes (ex.:
    Frutal) não baterem 429 na 2ª chamada, sem atrasar editais pequenos/
    médios (ex.: Paulínia, Ouroeste), que ficam bem abaixo do limite e não
    esperam nada.

    Devolve sempre o mesmo formato de dict:
    {"comparacao_possivel": bool, "motivo_impossibilidade": str | None,
    "inconsistencias": list[dict]}. Nunca levanta exceção pra "TR não
    identificado" — isso é uma resposta válida do produto (como
    "encontrado": False no Q&A), não um erro de sistema. Ainda levanta
    ProcessoNaoEncontradoError se o processo não existir, e repassa
    ContextoGrandeDemaisError/RespostaIAError do llm_client sem alterar, se
    alguma das duas chamadas falhar.

    Quando a comparação roda (com ou sem achado), limpa inconsistências de
    uma rodada anterior antes de salvar as novas — não fica acumulando
    resultado de detecção antiga junto com a nova.

    Fase 2, Camada 2 (UI): toda vez que a função COMPLETA (os três "return"
    abaixo, com ou sem achado, possível ou não), registra o resultado nas
    colunas "inconsistencias_*" de "processo" (ver
    app.db.repositorio.atualizar_status_deteccao_inconsistencias) — é assim
    que a tela do checklist sabe diferenciar "nunca verificado" de
    "verificado, sem achado". Se alguma das duas chamadas à IA falhar
    (rede, ContextoGrandeDemaisError, JSON malformado), a exceção sobe
    ANTES desse registro — a rodada não "completou", então o status
    anterior (se houver) fica como estava, não é substituído por um
    resultado que não aconteceu de verdade.
    """
    processo = obter_processo(processo_id, caminho_banco=caminho_banco)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {processo_id} não existe")

    deteccao = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_banco)
    limpar_inconsistencias_do_processo(processo_id, caminho_banco=caminho_banco)

    if not deteccao.identificado:
        atualizar_status_deteccao_inconsistencias(
            processo_id, comparacao_possivel=False,
            motivo_impossibilidade=deteccao.motivo_nao_identificado,
            caminho_banco=caminho_banco,
        )
        return {
            "comparacao_possivel": False,
            "motivo_impossibilidade": deteccao.motivo_nao_identificado,
            "inconsistencias": [],
        }

    if not deteccao.paginas_edital:
        # Bloco de edital vazio (o marcador de TR caiu logo no início do
        # texto) — não há corpo de edital pra comparar contra o TR. Mesmo
        # princípio: não força comparação sem os dois lados de verdade.
        motivo = (
            "o bloco de edital ficou vazio (o marcador de TR foi "
            "encontrado logo no início do texto) — não há corpo de "
            "edital pra comparar contra o TR"
        )
        atualizar_status_deteccao_inconsistencias(
            processo_id, comparacao_possivel=False, motivo_impossibilidade=motivo,
            caminho_banco=caminho_banco,
        )
        return {
            "comparacao_possivel": False,
            "motivo_impossibilidade": motivo,
            "inconsistencias": [],
        }

    texto_edital_marcado = _montar_texto_marcado_por_pagina(deteccao.paginas_edital)
    texto_tr_marcado = _montar_texto_marcado_por_pagina(deteccao.paginas_tr)

    # Camada 3: 2 chamadas, não 1 — ver docstring da função e a decisão
    # registrada em planejamento-nexlicit-engine.md. Se a PRIMEIRA falhar
    # (rede, JSON malformado...), a exceção sobe direto — não faz sentido
    # gastar a segunda chamada se a primeira já não completou.
    #
    # Antes de chamar, mede o tamanho real (count_tokens, não estimativa)
    # pra decidir se vale espaçar as duas chamadas — ver docstring da
    # função e _tempo_de_espera_entre_execucoes. Se essa medição falhar
    # (ex.: rede), deixa a exceção subir igual às outras chamadas de IA —
    # sem tamanho medido não dá pra decidir com segurança, e simplesmente
    # seguir sem espera arriscaria repetir o 429 do golden test do Frutal.
    tokens_por_chamada = _contar_tokens_comparacao_ia(texto_edital_marcado, texto_tr_marcado)
    espera_segundos = _tempo_de_espera_entre_execucoes(tokens_por_chamada)

    execucao_1 = _detectar_inconsistencias_ia(texto_edital_marcado, texto_tr_marcado)
    if espera_segundos:
        logger.info(
            "espaçando execução dupla do processo %s: %ss (tokens por "
            "chamada medidos: %s)", processo_id, espera_segundos, tokens_por_chamada,
        )
        time.sleep(espera_segundos)
    execucao_2 = _detectar_inconsistencias_ia(texto_edital_marcado, texto_tr_marcado)
    inconsistencias = _mesclar_inconsistencias(execucao_1, execucao_2)

    if inconsistencias:
        salvar_inconsistencias(processo_id, inconsistencias, caminho_banco=caminho_banco)

    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=True, motivo_impossibilidade=None,
        caminho_banco=caminho_banco,
    )

    return {
        "comparacao_possivel": True,
        "motivo_impossibilidade": None,
        "inconsistencias": inconsistencias,
    }
