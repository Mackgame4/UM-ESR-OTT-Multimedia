# esr-ott

Universidade do Minho ESR TP2: application-level overlay network for multimedia
delivery in the CORE emulator. Python standard library only. TCP carries control
messages; UDP video streaming is reserved for a later stage.

## Estratégia 2 — controlador e routers a pedido

**Server1 é o servidor e bootstrapper e deve permanecer ativo.** O utilizador
inicia manualmente apenas o servidor e os clientes. Server1 inicia os processos
overlay nos routers necessários quando recebe o registo de um cliente.

Não existe configuração manual do overlay. Server1 lê a topologia física de
[`topology/topology.xml`](topology/topology.xml) e identifica o router de acesso
de cada cliente usando BFS. Só esse router executa a aplicação overlay e liga-se
diretamente ao servidor. Os routers intermédios encaminham os pacotes por IP,
através das rotas do CORE; não precisam de executar a aplicação.

Para C1, o caminho calculado é:

```text
Server1 <-> R3 <-> C1
              <-> C2
```

1. C1 envia REGISTER para Server1, identificando-se.
2. Server1 inicia a aplicação em R3 através de `vcmd`, no router existente
   da sessão CORE. Não cria novos routers físicos.
3. R3 regista-se e recebe Server1 como vizinho.
4. C1 recebe R3 como vizinho e liga-se diretamente a ele por TCP.
5. Cada ligação é full-duplex; apenas a extremidade mais distante de Server1
   inicia a ligação.

Só são ativados os routers dos caminhos pedidos. C2 reutiliza os mesmos routers
de C1. Os restantes routers continuam a encaminhar IP sem executar o overlay.

## Executar na VM CORE

Abrir `topology/topology.xml` no CORE da VM e iniciar a emulação. Python 3 e o
projeto devem estar acessíveis no filesystem partilhado dos nós. Confirmar que
C1 consegue executar `ping 10.0.4.20` antes de arrancar a aplicação.

Na raiz do projeto, no terminal CORE de **Server1**:

```sh
python3 -m src.main --controller --id Server1
```

Depois, manualmente no terminal CORE de **C1**:

```sh
python3 -m src.main 10.0.4.20 --id C1
```

No terminal de **C2**, executar `python3 -m src.main 10.0.4.20 --id C2`.
Ao atualizar uma sessão que usava processos antigos em R9/R7/R4, reiniciar a
sessão CORE antes de executar estes comandos para limpar esses processos.

Não é necessário executar comandos nos routers. O identificador deve corresponder
ao nome no XML; por omissão usa-se o hostname.

O bootstrapper usa TCP **5001**; os vizinhos overlay usam TCP **5000**. A sessão
CORE é identificada automaticamente se existir uma única sessão com os canais
dos nós esperados. Se necessário, indicar o diretório real:

```sh
python3 -m src.main --controller --id Server1 --core-session /tmp/pycore.12345
```

`--topology CAMINHO` escolhe outro XML físico. O lançamento remoto usa os canais
`vcmd` dentro da VM CORE: um endereço IP, por si só, não permite iniciar processos
noutra máquina. Os logs ficam no diretório da sessão, por exemplo `R3-ott.log`.

## Registo, abandono e falhas

- Cada nó renova o registo a cada **5 segundos**. Após **15 segundos** sem
  renovação, deixa de aparecer em `/members` no servidor.
- `/quit` e Ctrl+C enviam LEAVE. Uma falha abrupta é tratada pela expiração
  do registo e pelo fecho da ligação TCP.
- As renovações voltam a tentar ligar aos vizinhos indisponíveis, incluindo
  routers que ainda estão a arrancar.
- Os routers iniciados ficam disponíveis até o servidor terminar, mesmo depois
  de sair o último cliente. Ainda não existe remoção automática por falta de uso.
- Server1 é um ponto único de falha: novos registos e a criação de routers
  dependem dele. Ligações existentes entre outros nós podem continuar abertas.
- Os caminhos são calculados a partir do XML no arranque; ainda não existe
  recálculo automático após alterações físicas ou falhas de routers.

## Comandos e protocolo

- `/peers`: listar vizinhos ligados.
- `/members`: listar clientes ativos e o estado dos routers, apenas no controlador.
- `/hello`: enviar HELLO aos vizinhos.
- Texto livre em Server1: multicast MESSAGE para todos os clientes ligados.
- Texto livre num cliente: enviar MESSAGE apenas para Server1 através do router.
- `/connect HOST:PORT`: acrescentar um vizinho manualmente.
- `/quit`: sair.

Exemplo de `/members` com C1 e C2 ligados:

```text
Server1 (CONTROLLER)
C1 (OVERLAY CLIENT)
C2 (OVERLAY CLIENT)
R3 (OVERLAY ROUTER)
R4 (UNDERLAY ONLY)
R7 (UNDERLAY ONLY)
R9 (UNDERLAY ONLY)
```

São mostrados todos os routers conhecidos do XML. `UNDERLAY ONLY` identifica
routers sem aplicação overlay iniciada/registada pelo controlador; não exige
Python nesses nós e não indica uma verificação da sua disponibilidade IP.
`OVERLAY NOT REGISTERED` indica que o lançamento foi pedido mas ainda não houve
registo; `OVERLAY INACTIVE` indica um nó anteriormente registado que saiu ou cuja
lease expirou. Um router inativo não é confundido com um router apenas underlay.

O multicast é feito pela aplicação sobre TCP: Server1 envia uma cópia por ramo
overlay e R3 replica-a para C1 e C2. Mensagens de C1 seguem apenas para Server1,
sem eco para C1 nem entrega a C2. Routers apenas reencaminham; não originam texto.
`/hello` continua a testar apenas o vizinho direto. O modo manual anterior mantém
o envio apenas para vizinhos diretos. Streaming de vídeo ainda não está implementado.

Cada MESSAGE encaminhado preserva o identificador da origem e inclui `delivery`
(`MULTICAST` ou `UPSTREAM`), `message_id` e `ttl`. Os nós descartam duplicados
recentes e limitam os saltos. A identidade do vizinho TCP é estabelecida pelo
HELLO e não muda quando passa uma mensagem de outra origem.

As mensagens são objetos JSON UTF-8 terminados por newline, até 65536 bytes.
Tipos: HELLO, MESSAGE, REGISTER, NEIGHBOURS, LEAVE, ACK e ERROR. JSON é apenas
a codificação das mensagens TCP; não existe ficheiro JSON da topologia overlay.
O registo assume um ambiente CORE de confiança, sem autenticação dos IDs.

## Modo manual anterior

Continua disponível para testes locais:

```sh
python -m src.main --id A
python -m src.main --id B --port 5002 --peer 127.0.0.1:5000
```

## Testes

Testes com sockets locais, sem CORE:

```sh
python -m unittest discover -s tests
```

Para testar dentro da VM CORE, consultar [o guia](topology/README.md).
