-- Schema do banco SQLite local do NexLicit Engine.
-- CREATE TABLE IF NOT EXISTS: seguro rodar em toda conexão nova, não recria
-- nada se as tabelas já existirem.
--
-- PRAGMA foreign_keys também é ativado aqui por documentação, mas isso
-- sozinho NÃO garante nada: é um ajuste por conexão do SQLite, então o
-- código Python (app/db/conexao.py) ativa de novo em toda conexão aberta.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS processo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    orgao TEXT,
    modalidade TEXT,
    objeto TEXT,
    valor_estimado REAL,
    data_sessao TEXT,
    plataforma TEXT,
    criado_em TEXT NOT NULL,
    -- Fase 2 (motor de inconsistências), Camada 2: status da ÚLTIMA rodada
    -- de detecção — não dá pra saber, só olhando a tabela "inconsistencia"
    -- (que só guarda ACHADOS), se a comparação nunca rodou, se rodou e não
    -- achou nada, ou se rodou e nem foi possível comparar (TR não
    -- identificado, ver app/inconsistencias/limite_tr.py). Os três estados
    -- da UI dependem de saber diferenciar isso:
    -- "inconsistencias_verificado_em" NULL = nunca rodou.
    -- NOT NULL + "inconsistencias_comparacao_possivel" = 0 = rodou, TR não
    -- identificado (mensagem do motivo em "inconsistencias_motivo_...").
    -- NOT NULL + comparacao_possivel = 1 + nenhuma linha em "inconsistencia"
    -- pra este processo = rodou, comparou, não achou nada (estado positivo).
    inconsistencias_verificado_em TEXT,
    inconsistencias_comparacao_possivel INTEGER,
    inconsistencias_motivo_impossibilidade TEXT,
    -- Passo 3/6 (extração do checklist via IA): mesmo problema da Camada 2
    -- acima, resolvido do mesmo jeito. Sem isso, "0 exigências" não dá pra
    -- diferenciar de "nunca analisado" nem de "análise tentada e falhou
    -- antes de salvar qualquer exigência" (ex.: erro 503 da API do Gemini)
    -- — os três casos chegavam com a mesma contagem zerada na listagem
    -- (achado no levantamento visual do dashboard, 13/08/2026).
    -- "checklist_verificado_em" NULL = nunca tentou analisar.
    -- NOT NULL + "checklist_sucesso" = 0 = tentou e falhou (mensagem do
    -- erro em "checklist_erro").
    -- NOT NULL + checklist_sucesso = 1 = tentou e completou (0 exigências
    -- nesse caso é resultado real, não falha).
    checklist_verificado_em TEXT,
    checklist_sucesso INTEGER,
    checklist_erro TEXT,
    -- Fase 4, Camada 1 (minuta de proposta, decisão de 19/08/2026): campo
    -- manual, texto livre (ex.: "60 dias", "90 dias corridos"). Não é
    -- extraído automaticamente do checklist -- investigação (Camada 0)
    -- contra os processos reais achou a cláusula ausente em 3 de 12 e,
    -- mesmo presente, embutida num parágrafo maior demais pra citar
    -- isolada em 1 de 7 casos -- confiança insuficiente pra puxar sem
    -- revisão humana. Preenchido na tela /processos/{id}/planilha-preco,
    -- junto do resto do dado comercial digitado por gente.
    validade_proposta TEXT
);

CREATE TABLE IF NOT EXISTS arquivo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    nome_arquivo TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('pdf', 'docx')),
    num_paginas INTEGER,
    texto_extraido TEXT
);

-- Fase 2, Camada 0: texto bruto por página, pro assistente de perguntas
-- (long-context, sem embeddings) montar o contexto e citar de onde tirou
-- cada resposta. "arquivo.texto_extraido" já guarda o texto inteiro, mas
-- concatenado sem fronteira de página — aqui cada página vira uma linha
-- própria, pra dar pra citar "página N" na resposta.
--
-- "numero_pagina" é NULLABLE: PDF tem página física de verdade (1, 2, 3...),
-- mas DOCX não guarda número de página no arquivo (só é calculado na hora
-- de imprimir/exibir no Word — ver comentário na classe Bloco, em
-- app/extracao/extrator.py) — pra DOCX isso fica NULL e quem cita usa
-- "localizador" (ex.: "parágrafo 5"), que está preenchido sempre, PDF ou
-- DOCX.
CREATE TABLE IF NOT EXISTS texto_pagina (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    arquivo_id INTEGER NOT NULL REFERENCES arquivo (id),
    numero_pagina INTEGER,
    localizador TEXT NOT NULL,
    texto TEXT NOT NULL
);

-- "pagina" é TEXT (não INTEGER): o validador (Passo 4) pode devolver um
-- número de página simples ("9") ou um intervalo quando o trecho atravessa
-- uma quebra de página ("9-10") — texto cobre os dois casos sem ambiguidade.
CREATE TABLE IF NOT EXISTS exigencia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    categoria TEXT NOT NULL,
    descricao TEXT NOT NULL,
    base_legal TEXT,
    trecho TEXT,
    pagina TEXT,
    localizador TEXT,
    arquivo_origem_id INTEGER REFERENCES arquivo (id),
    obrigatorio_para TEXT NOT NULL CHECK (obrigatorio_para IN ('todos', 'vencedor')),
    confianca TEXT NOT NULL CHECK (confianca IN ('localizado', 'inferido')),
    cruzou_pagina INTEGER NOT NULL DEFAULT 0,
    ocorrencias_encontradas INTEGER NOT NULL DEFAULT 0,
    status_check TEXT NOT NULL DEFAULT 'pendente' CHECK (status_check IN ('pendente', 'ok', 'nao_aplica')),
    observacao_usuario TEXT,
    -- Preenchido pela IA só quando esta exigência é UMA ENTRE VÁRIAS formas
    -- alternativas de satisfazer a mesma exigência de fundo (ex.: "contrato
    -- social" e "registro de empresário individual" nunca vêm juntos no
    -- mesmo licitante — é uma OU outra, dependendo do tipo societário). NULL
    -- para exigência avulsa, sem alternativa. Exigências da MESMA categoria
    -- com o MESMO texto aqui viram um card só, com um checkbox só, na tela
    -- (app/rotas/paginas.py, _agrupar_por_categoria) — mas cada uma continua
    -- guardada como linha própria, com seu próprio trecho literal, porque o
    -- validador do Passo 4 precisa localizar cada trecho individualmente.
    grupo_hipoteses TEXT
);

-- Requisitos técnicos por item (Passo 8): extração determinística por
-- palavra-chave fixa (app/extracao/exigencias_item.py), sem IA. Por isso não
-- tem coluna de confiança como "exigencia" — o trecho é sempre literal, por
-- construção, nunca passa por um modelo que possa alucinar.
--
-- "pagina" aqui é INTEGER, não TEXT como em "exigencia": o fatiamento por
-- item (app/extracao/tabela_itens.py) nunca produz um intervalo tipo
-- "9-10" — isso só existe no validador do Passo 4, que junta blocos
-- vizinhos quando um trecho atravessa página; o fatiamento por item não
-- faz esse tipo de junção.
CREATE TABLE IF NOT EXISTS requisito_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    arquivo_origem_id INTEGER REFERENCES arquivo (id),
    numero_item INTEGER NOT NULL,
    categoria TEXT NOT NULL,
    gatilho TEXT NOT NULL,
    trecho TEXT NOT NULL,
    pagina INTEGER,
    localizador TEXT,
    ocorrencias_encontradas INTEGER NOT NULL DEFAULT 1
);

-- Fase 4, Camada 1 (planilha de preço): catálogo mínimo de itens, gerado
-- automaticamente pelo mesmo fatiamento determinístico do Passo 8
-- (app/extracao/tabela_itens.py, reaproveitado -- sem IA nova). "texto_bruto"
-- é o texto ORIGINAL não estruturado do item, exatamente como saiu da
-- tabela do edital (número + descrição + unidade + quantidade tudo
-- misturado, sem separação de coluna confiável -- mesmo problema já
-- documentado no backlog do Pirangi, seção 13 do planejamento). Decisão B
-- (16/08/2026): não tentar separar isso em colunas; guarda o texto bruto
-- inteiro como referência de descrição, e pede quantidade digitada por
-- gente (ver preco_item) em vez de tentar extrair um número não confiável
-- daqui.
--
-- Só populado por processo NOVO ou REPROCESSADO depois desta mudança —
-- processo já analisado antes não ganha catálogo retroativamente (mesmo
-- princípio de checklist_verificado_em/inconsistencias_verificado_em:
-- dado derivado só existe se o pipeline já rodou depois de existir).
CREATE TABLE IF NOT EXISTS item_catalogo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    numero INTEGER NOT NULL,
    texto_bruto TEXT NOT NULL,
    pagina INTEGER,
    localizador TEXT
);

-- Fase 4, Camada 1: preço digitado por gente, item a item -- nunca
-- inventado, nunca extraído. "numero_item" (não FK pro id de
-- item_catalogo) é de propósito o mesmo padrão já usado em
-- requisito_item.numero_item: liga pelo NÚMERO do item do edital, não
-- pela chave interna da linha que o guarda. UNIQUE(processo_id,
-- numero_item) permite salvar por "upsert" (INSERT ... ON CONFLICT) --
-- cria a linha na primeira vez que a pessoa digita algo naquele item,
-- atualiza nas vezes seguintes, sem precisar buscar um id antes.
--
-- "marca"/"fabricante"/"modelo" (Fase 4, Camada 1 da minuta de proposta,
-- 19/08/2026): mesmo ponto de entrada por item que quantidade/preço --
-- mesma tela, mesmo padrão de salvar ao sair do campo -- em vez de
-- tabela nova só pra isso. Todos opcionais: nem todo item exige as três
-- coisas (ex.: serviço não tem marca/modelo de produto).
CREATE TABLE IF NOT EXISTS preco_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    numero_item INTEGER NOT NULL,
    quantidade REAL,
    preco_unitario REAL,
    marca TEXT,
    fabricante TEXT,
    modelo TEXT,
    UNIQUE (processo_id, numero_item)
);

-- Fase 2 (motor de inconsistências edital-vs-TR), Camada 1: contradições
-- reais entre o corpo do edital e o Termo de Referência (TR), encontradas
-- pela IA a partir do limite identificado na Camada 0
-- (app/inconsistencias/limite_tr.py). "pagina_edital"/"pagina_tr" são
-- INTEGER, não TEXT como em "exigencia": vêm direto do marcador "[PÁGINA
-- N]" que a IA cita, sem passar por um validador que junte blocos vizinhos
-- (mesmo raciocínio de "requisito_item.pagina", não o de "exigencia.pagina").
CREATE TABLE IF NOT EXISTS inconsistencia (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    processo_id INTEGER NOT NULL REFERENCES processo (id),
    tipo TEXT NOT NULL CHECK (tipo IN ('quantidade', 'valor', 'prazo', 'especificacao_tecnica', 'administrativo')),
    descricao TEXT NOT NULL,
    trecho_edital TEXT NOT NULL,
    pagina_edital INTEGER,
    trecho_tr TEXT NOT NULL,
    pagina_tr INTEGER
);

-- Fase 4, Camada 0: cadastro de empresas fornecedoras — quem vai assinar
-- os documentos gerados (Camada 1 em diante: declarações, depois minuta
-- de proposta, planilha de preço, recurso administrativo). Suporta
-- múltiplas empresas (o Bruno atende clientes diferentes); a escolha de
-- qual empresa entra em cada documento acontece na hora de gerar, não
-- aqui.
--
-- Só "razao_social" e "cnpj" são NOT NULL: são o mínimo pra identificar
-- a empresa de verdade (mesmo princípio de "processo.nome" ser o único
-- campo obrigatório lá). O resto fica opcional na entrada — a geração de
-- documento (Camada 1) que vai exigir o que precisar na hora de montar
-- cada declaração, não o cadastro.
--
-- "endereco" é um campo de texto único, não separado em rua/número/
-- cidade/etc: as declarações do próprio edital fictício (Anexo II) só
-- citam razão social e CNPJ, nunca o endereço linha a linha — não haveria
-- o que reaproveitar de campos separados que justifique a complexidade
-- a mais.
--
-- "regime_tributario" é texto livre (sem CHECK como confianca/tipo/etc.):
-- ME/EPP/Normal são exemplos, não uma lista fechada de verdade (existe
-- também MEI, Simples Nacional, Lucro Presumido...) — travar isso agora
-- arriscaria bloquear cadastro de empresa real mais adiante.
CREATE TABLE IF NOT EXISTS empresa (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    razao_social TEXT NOT NULL,
    nome_fantasia TEXT,
    cnpj TEXT NOT NULL,
    endereco TEXT,
    representante_legal_nome TEXT,
    representante_legal_cpf TEXT,
    representante_legal_cargo TEXT,
    telefone TEXT,
    email TEXT,
    regime_tributario TEXT,
    criado_em TEXT NOT NULL
);
