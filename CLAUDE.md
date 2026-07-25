# NexLicit Engine — instruções do projeto

Analisador de edital de licitação para uso interno da NexLicit.
Escopo: somente Lei 14.133/2021, todas as modalidades.
Stack: Python + FastAPI, HTML+Tailwind via Jinja2, SQLite, PyMuPDF,
python-docx. IA por camada trocável, padrão Gemini Flash (free tier).

## Regras de trabalho (valem sempre)
1. Explicar antes de criar ou alterar arquivo, e esperar minha aprovação.
2. Uma coisa de cada vez, sem adiantar passos futuros.
3. Sem código mágico: explicar o porquê em linguagem simples (sou iniciante).
4. Comentários no código em português.
5. Não inventar detalhes de bibliotecas ou APIs. Na dúvida de versão ou nome
   de pacote, perguntar em vez de chutar.

## Quando usar cada ferramenta
- LSP de Python: sempre. Corrigir o que ele apontar antes de me mostrar código.
- Testes: rodar os testes do módulo depois de alterá-lo. Se não houver teste
  para o que mudei, me avisar e sugerir um.
- Antes de qualquer commit: conferir que nenhuma chave de API, arquivo .env,
  banco .db ou edital real está sendo adicionado ao git. Se estiver, parar e
  me avisar. Nunca commitar segredo.
