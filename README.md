# NexLicit Engine

Ferramenta de análise automatizada de editais de licitação pública, construída
sobre a Lei 14.133/2021. Lê o edital, extrai o checklist completo de
habilitação, responde perguntas em linguagem natural sobre o conteúdo, e
compara o corpo do edital com o Termo de Referência em busca de
inconsistências.

**[Ver demo ao vivo](#)** *(link do Render, adicionar após o deploy)*

## O problema

Fornecedores que participam de licitações públicas perdem horas lendo
editais de 50 a 150 páginas para montar o checklist de documentos exigidos,
conferir prazos, e garantir que a proposta atende exatamente ao que foi
pedido. Um documento esquecido ou um prazo mal lido custa a inabilitação do
processo inteiro.

## O que o Engine faz

- **Checklist automatizado de habilitação.** Extrai as exigências de
  habilitação jurídica, fiscal, econômico-financeira, técnica, declarações
  e requisitos de proposta, cada uma com o trecho literal do edital e a
  página de origem, para conferência rápida.
- **Requisitos técnicos por item.** Para editais com tabela de itens
  (amostra, registro sanitário, garantia, certificação...), agrupa por
  categoria em vez de por item, evitando repetir o mesmo texto centenas de
  vezes em editais grandes.
- **Perguntas em linguagem natural.** Pergunta qualquer coisa sobre o
  edital e recebe uma resposta com página de origem — ou um "não
  encontrado" honesto quando a informação não está no texto. Sem RAG, sem
  embeddings: o contexto completo do documento vai direto para o modelo.
- **Motor de inconsistências.** Compara o corpo do edital com o Termo de
  Referência em busca de contradições reais (prazo, valor, quantidade,
  especificação técnica) — o tipo de erro humano de copiar/colar sem
  atualizar um número, que só aparece comparando os dois documentos lado a
  lado.

## Por que isso importa

Toda funcionalidade de IA no Engine segue um princípio simples: **nunca
inventar o que não está no texto**. Cada resposta, cada exigência extraída,
cada inconsistência encontrada precisa vir acompanhada de uma citação
literal e verificável. Quando a informação não existe no documento, o
sistema diz isso claramente, em vez de completar com conhecimento geral.
Esse guarda-corpo foi testado contra perguntas adversariais — premissas
falsas, conhecimento jurídico genérico, ambiguidade proposital — e
manteve-se consistente em todos os casos.

## Stack

Python, FastAPI, SQLite, PyMuPDF/python-docx (extração de PDF e DOCX),
Jinja2 (templates), Gemini Flash (extração estruturada e comparação por
long-context).

## Sobre o projeto

Construído por [Bruno](https://www.linkedin.com/company/nexlicit/),
ex-pregoeiro, ex-fornecedor (500+ processos competidos) e hoje consultor de
licitações, combinando experiência de mais de uma ponta do processo
licitatório com desenvolvimento assistido por IA. O Engine faz parte de um
portfólio de ferramentas que unem conhecimento de domínio em compras
públicas com engenharia de software — ao lado do
[Radar NexLicit](https://radarnexlicit.streamlit.app), que monitora editais
abertos em tempo real.

## Aviso sobre o demo

O material de demonstração público (`demo/edital_ficticio/`) é inteiramente
fictício — nenhum órgão, servidor, fornecedor ou processo real. Contém uma
divergência proposital de prazo entre o corpo do edital e o Anexo I,
inserida de propósito para demonstrar o motor de inconsistências em ação.

## Configuração após clonar

Este repositório usa um hook de pré-commit versionado (pasta `.githooks/`)
que roda o [gitleaks](https://github.com/gitleaks/gitleaks) para bloquear
commits que contenham segredos (chaves de API, tokens etc.). A pasta padrão
`.git/hooks/` não vai pro Git, então esse passo precisa ser feito manualmente
uma vez em cada máquina:

```powershell
winget install Gitleaks.Gitleaks
git config core.hooksPath .githooks
```

Sem o `gitleaks` instalado, o hook apenas avisa e deixa o commit passar —
ele não substitui a revisão manual antes de commitar.
