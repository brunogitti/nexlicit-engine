# Instância única do Jinja2Templates, num módulo neutro sem depender de
# nenhuma rota — assim tanto app/rotas/processos.py (que precisa renderizar
# HTML na negociação de conteúdo de GET /processos) quanto app/rotas/
# paginas.py podem importar daqui sem criar import circular entre os dois.

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
