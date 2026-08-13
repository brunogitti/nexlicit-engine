# Script usado na Camada 0 (Fase 3, embeddings como fallback) pra medir
# tempo real de embedding em CPU, sem GPU. Recurso PAUSADO por decisão
# (12/08/2026) antes de qualquer implementação -- ver a entrada
# "Embeddings/busca semântica (Fase 3) — pausado por decisão" em
# planejamento-nexlicit-engine.md (seção 13) pro motivo completo, o
# resultado desta medição e o gatilho claro pra retomar.
#
# Preservado aqui só como referência/reprodutibilidade -- NÃO faz parte
# do pipeline do projeto, não é importado por nada em app/. Se retomar o
# trabalho, este script é o ponto de partida pra não remedir do zero.
#
# Como rodar de novo (ambiente isolado, fora do venv do projeto -- as
# dependências abaixo não estão em requirements.txt):
#   python -m venv venv_embeddings   (num caminho CURTO -- caminho longo
#                                      quebra a instalação do torch no
#                                      Windows por limite de path)
#   venv_embeddings\Scripts\pip install torch==2.8.0 \
#       --index-url https://download.pytorch.org/whl/cpu
#   venv_embeddings\Scripts\pip install sentence-transformers
#   # gerar um "paginas.json" (lista de strings, uma por página) a partir
#   # de app.extracao.extrator.extrair_texto() de qualquer edital real
#   venv_embeddings\Scripts\python medir_embedding.py
#
# torch==2.8.0 é obrigatório: 2.9.0+ não carrega no Windows (WinError
# 1114, bug aberto do próprio PyTorch, não é falta de dependência da
# máquina -- ver link no planejamento).
import json
import time

print("Carregando SentenceTransformer (import)...")
inicio_import = time.time()
from sentence_transformers import SentenceTransformer
print(f"  import: {time.time() - inicio_import:.1f}s")

print("\nCarregando o modelo BAAI/bge-m3 (baixa na 1a vez, ~2.27GB)...")
inicio_carga = time.time()
modelo = SentenceTransformer("BAAI/bge-m3", device="cpu")
duracao_carga = time.time() - inicio_carga
print(f"  modelo carregado em memória: {duracao_carga:.1f}s")

with open("paginas.json", encoding="utf-8") as f:
    paginas = json.load(f)

print(f"\n{len(paginas)} páginas carregadas ({sum(len(p) for p in paginas):,} caracteres no total)")

# Embedding de UMA página só primeiro, pra medir custo por página isolado
# (sem o overhead de warm-up do modelo diluído no resultado).
print("\n--- 1 página isolada (a maior) ---")
pagina_maior = max(paginas, key=len)
print(f"tamanho da maior página: {len(pagina_maior)} caracteres")
inicio = time.time()
vetor = modelo.encode(pagina_maior, show_progress_bar=False)
duracao_uma = time.time() - inicio
print(f"tempo: {duracao_uma:.2f}s | dimensão do vetor: {len(vetor)}")

# Documento inteiro, em lote -- forma como rodaria de verdade na Camada 1
# (1 embedding por linha de texto_pagina).
print(f"\n--- documento inteiro ({len(paginas)} páginas, em lote) ---")
inicio = time.time()
vetores = modelo.encode(paginas, show_progress_bar=True, batch_size=8)
duracao_total = time.time() - inicio
print(f"\ntempo total: {duracao_total:.1f}s ({duracao_total/60:.1f} min)")
print(f"tempo médio por página: {duracao_total/len(paginas):.3f}s")
print(f"páginas por segundo: {len(paginas)/duracao_total:.2f}")
print(f"dimensão de cada vetor: {vetores.shape}")

print("\n=== RESUMO ===")
print(f"carga do modelo (1x, fica em memória depois): {duracao_carga:.1f}s")
print(f"1 página isolada (inclui warm-up): {duracao_uma:.2f}s")
print(f"{len(paginas)} páginas (regime aquecido): {duracao_total:.1f}s")
