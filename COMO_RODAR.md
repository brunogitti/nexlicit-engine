# Como rodar o NexLicit Engine no dia a dia

Guia curto pra usar o Engine de verdade, com o seu banco real
(`nexlicit.db`, com os processos que você já analisou) — não é sobre
teste, não é sobre o demo público.

## 1. Instalar as dependências (só na primeira vez, ou depois de puxar mudança nova)

Com o terminal aberto na pasta `nexlicit-engine`:

```powershell
.\venv\Scripts\pip install -r requirements.txt
```

## 2. Conferir o `.env`

O arquivo `.env` (na raiz do projeto, ao lado deste guia) precisa ter pelo
menos `GEMINI_API_KEY` preenchida — sem ela, qualquer análise nova falha.
Se você já usa o Engine, isso já deve estar configurado; `.env.example`
mostra o formato esperado, caso precise recriar.

## 3. Rodar o servidor

Sempre com o terminal na pasta `nexlicit-engine`, este é o comando:

```powershell
$env:NEXLICIT_USE_BANCO_REAL="1"
.\venv\Scripts\python.exe -m uvicorn app.main:app
```

**Por que a primeira linha existe:** o Engine tem uma trava de segurança
que recusa conectar no banco padrão (`nexlicit.db`) se essa variável não
estiver definida — ela existe justamente pra impedir que um script
avulso escreva no seu banco real por acidente. Pro uso normal do dia a
dia, você *quer* o banco real, então define a variável antes de subir o
servidor. **Se você esquecer essa linha, o servidor mostra um erro claro
ao tentar acessar qualquer processo — não é bug, é a trava funcionando
como projetada.**

`$env:NEXLICIT_USE_BANCO_REAL="1"` vale só pra aquela janela do
terminal (some quando você fecha); não precisa (e não deve) colocar essa
variável dentro do `.env` — motivo documentado no próprio `.env.example`.

## 4. Abrir no navegador

```
http://localhost:8000
```

Essa é a porta padrão do `uvicorn` quando nenhuma é especificada — não
precisa configurar nada extra. Você deve ver a lista dos seus processos
já analisados.

## 5. Analisar um edital novo

1. Clique em **"+ Novo processo"** (canto superior direito).
2. Preencha o que souber (nome do processo é o único campo obrigatório) e
   selecione o(s) arquivo(s) do edital (PDF ou DOCX).
3. Clique em **"Criar e analisar"** — você é levado pra uma tela de espera
   que mostra o progresso; pode levar de 1 a alguns minutos, dependendo
   do tamanho do edital.
4. Quando terminar, você cai direto no checklist pronto pra conferir.

## Para encerrar

`Ctrl+C` no terminal onde o servidor está rodando.

## Extra: modo de desenvolvimento (`--reload`)

Se um dia você (ou alguém no seu nome) estiver mexendo no código e quiser
que o servidor reinicie sozinho a cada mudança de arquivo, adicione
`--reload` no fim do comando do Passo 3. Pro uso diário normal (só usar o
Engine, sem mexer no código), não precisa disso.
