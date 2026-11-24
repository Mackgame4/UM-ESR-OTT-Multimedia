# esr-ott

Projeto ESR TP2: rede overlay multicast para distribuição multimédia no CORE.
O plano de controlo e a demonstração de mensagens usam TCP. O vídeo MJPEG usa
UDP e a mesma árvore multicast. O cliente gráfico requer Pillow e Tkinter;
o servidor, os routers e o cliente sem janela usam a biblioteca standard.
A monitorização na VM Linux requer `ping` (iputils).

## Estratégia 2 — servidor como bootstrapper e controlador

O **servidor de streaming é também o bootstrapper do seu overlay**. Não existe
um bootstrapper independente num router: sem o servidor não existe a fonte do
stream. Server1 ou Server2 pode assumir este papel através de `--controller`.
Cada execução gere o seu próprio overlay; não existe eleição, replicação de
estado ou transferência automática de clientes entre servidores.

O XML `topology/topology.xml` fornece apenas os nomes, endereços, interfaces e
ligações candidatas iniciais. O estado atual das ligações é descoberto através
de sondas periódicas; cortar uma ligação não exige editar ou reler o XML.
Ligações completamente novas, ausentes do XML, ainda exigem atualizar o inventário.

### Funcionamento

1. O servidor inicia automaticamente um agente de controlo em cada router da
   sessão CORE. Estes agentes registam-se e recebem os vizinhos físicos a testar.
2. Servidor, routers e clientes testam os seus vizinhos com `ping`, preso à
   interface local (`-I`) e limitado a um salto IP (`-t 1`). Um percurso alternativo
   do OSPF não faz uma ligação direta cortada parecer operacional.
3. Os relatórios seguem para o servidor em renovações REGISTER. Uma ligação só
   está disponível se ambos os extremos a reportarem operacional e tiverem registo
   válido. Relatórios ausentes não são interpretados como ligações disponíveis.
4. Ao iniciar, o cliente regista-se e envia explicitamente ROUTE_REQUEST para
   a stream `default`. `/unsubscribe` retira o pedido e `/subscribe` volta a ativá-lo.
5. O servidor calcula uma árvore de caminhos mínimos com BFS sobre o grafo vivo.
   A métrica é **número de saltos overlay**, custo unitário por ligação candidata
   entre dispositivos. Switches são transparentes e clientes não servem de trânsito.
6. Apenas os caminhos de clientes subscritos e alcançáveis integram a árvore de
   dados. O servidor distribui a cada nó o pai, os filhos e a versão da árvore.
7. Ao detetar um corte, uma recuperação ou uma lease expirada, recalcula as rotas.
   Os nós recolhem as atualizações nas renovações, fecham ligações obsoletas e
   estabelecem as novas. Os empates são resolvidos pela ordem dos nomes dos nós.

Os relatórios são recolhidos aproximadamente a cada **2 segundos**, acrescidos
do tempo das sondas/ligações. As leases expiram após **15 segundos** sem renovação.
Uma falha de ligação pode ser detetada por qualquer dos extremos; para a recuperar,
ambos precisam de voltar a confirmar disponibilidade.

### Agentes de controlo e distribuição multicast

Para descobrir alternativas sem depender exclusivamente do OSPF, os routers
candidatos precisam de um agente Python de controlo. A otimização anterior de
executar Python apenas em R3 foi substituída por esta separação:

- **CONTROL ONLY**: agente registado e a testar ligações, sem ramo de dados ativo.
- **OVERLAY ROUTER**: agente também a encaminhar dados para clientes subscritos.
- **OFFLINE**: não existe registo válido; não é utilizado em novos caminhos.

O processo de cada router é reutilizado; não é iniciado um processo por cliente
ou por pedido. Não existe tráfego de dados nos ramos sem subscritores, embora
continuem as sondas e as mensagens de controlo.

O servidor envia uma cópia por ramo partilhado. Um router replica essa cópia
apenas nas bifurcações. Mensagens de C1 sobem apenas até ao servidor e não são
reenviadas para C2. A origem é preservada; os routers não originam texto.

Cada MESSAGE tem ID, TTL e versão da árvore. Duplicados recentes são descartados.
Durante a convergência, mensagens de versões diferentes são descartadas para não
misturar árvores. Pode haver perda temporária; não existe instalação atómica,
buffering nem garantia de entrega durante uma mudança de rota.

## Executar na VM CORE

Abrir `topology/topology.xml`, iniciar a emulação e esperar pela conectividade IP.
O encaminhamento IP do CORE continua necessário para as mensagens de controlo
chegarem ao servidor. Os agentes descobrem a topologia disponível; não configuram
rotas IP nem substituem o OSPF.

Na raiz do projeto, no terminal de Server1:

```sh
python3 -m src.main --controller --id Server1
```

Manualmente, nos terminais dos clientes:

```sh
# C1
python3 -m src.main 10.0.4.20 --id C1
# C2
python3 -m src.main 10.0.4.20 --id C2
```

O servidor lança os agentes; não são necessários comandos manuais nos routers.
Bootstrap usa TCP **5001**, ligações de dados/controlo entre vizinhos usam TCP
**5000**. O projeto deve estar acessível no filesystem partilhado dos nós.

A sessão CORE é detetada automaticamente. Se existirem várias sessões, indicar
o diretório correto: `--core-session /tmp/pycore.12345`. `--topology CAMINHO`
permite escolher outro inventário XML. Os logs dos agentes ficam nesse diretório,
por exemplo `R3-ott.log`. Reiniciar a sessão após atualizar versões antigas para
remover os processos anteriores.

Também é possível usar Server2 como servidor/bootstrapper:

```sh
# Server2
python3 -m src.main --controller --id Server2
# Cliente que pretende essa fonte
python3 -m src.main 10.0.3.21 --id C1
```

Testar servidores separadamente: a execução simultânea de dois overlays nos
mesmos routers/portas não é suportada nesta fase.

## Comandos

- `/members`: no servidor, lista clientes e distingue CONTROL ONLY, OVERLAY ROUTER
  e OFFLINE. Deixamos de chamar UNDERLAY ONLY aos routers com agente de controlo.
- `/routes`: no servidor, mostra cliente, stream, caminho, saltos, estado ACTIVE
  ou UNREACHABLE e versão. Sem pedidos, a árvore de dados fica vazia.
- `/subscribe` e `/unsubscribe`: pedir ou deixar de receber a stream `default`.
- `/peers`: vizinhos TCP diretamente ligados; não representa todos os destinatários.
- `/hello`: testar apenas vizinhos diretos.
- `/ping`: no servidor, testar os percursos multicast e o retorno de cada cliente.
- Texto em servidor: multicast para os clientes subscritos e alcançáveis.
- Texto em cliente: enviar apenas para o servidor pelo caminho instalado.
- `/quit` ou Ctrl+C: enviar LEAVE e terminar. Falhas abruptas usam a lease.

O modo anterior de testes manuais continua disponível com `--peer HOST:PORT`,
sem controlo dinâmico nem reencaminhamento automático:

```sh
python -m src.main --id A
python -m src.main --id B --port 5002 --peer 127.0.0.1:5000
```

## Etapa 3 — teste dos percursos overlay

Com os clientes subscritos e as rotas estabilizadas, executar `/ping` no terminal
do servidor. O comando envia ROUTE_PING pela árvore multicast instalada, usando
as mesmas ligações TCP e os mesmos filhos que o encaminhamento de dados.

Cada router acrescenta o seu identificador à lista `path`, regista o percurso
no log e replica o teste para os seus filhos. Cada ramo acumula uma lista própria.
O cliente devolve ROUTE_PONG ao pai; os routers reencaminham-no para o servidor
e acumulam `return_path`, permitindo verificar também o percurso inverso.

Exemplo de resultado (os caminhos e tempos dependem da execução):

```text
C1: OK | Server1 -> R10 -> R7 -> R4 -> R3 -> C1 | RTT: 18.00 ms | teste=... | v3
C2: OK | Server1 -> R10 -> R7 -> R4 -> R3 -> C2 | RTT: 20.00 ms | teste=... | v3
```

O servidor espera até **3 segundos** pelas respostas e apresenta uma linha por
cliente subscrito. O RTT é medido apenas no servidor com relógio monotónico;
é diagnóstico e não altera a métrica de escolha de rotas.

- `OK`: o percurso observado corresponde ao caminho escolhido pelo controlador.
- `TIMEOUT`: não chegou resposta desse cliente dentro do prazo.
- `UNREACHABLE`: o controlador já não tinha caminho para esse cliente.
- `STALE`: a árvore mudou durante o teste; repetir após convergência.
- `PATH_MISMATCH`: chegou uma resposta, mas o percurso difere do caminho esperado.
- `CANCELLED`: o nó terminou enquanto o teste estava pendente.

As mensagens incluem ID de teste, TTL, versão e identificador do controlador.
Duplicados e respostas tardias não contam como novas respostas. Uma mensagem
de outra versão não é encaminhada pela árvore atual. Os logs ROUTE_PING e
ROUTE_PONG ficam nos logs dos agentes, por exemplo `R3-ott.log` na sessão CORE.

Este teste percorre o overlay de extremo a extremo; não usa as sondas ICMP de
monitorização e não fica limitado ao vizinho direto como `/hello`.

## Limites e testes

A stream `default` suporta vídeo MJPEG quando o servidor arranca com `--video`,
além da demonstração MESSAGE. A rede CORE é considerada de confiança, sem autenticação
dos IDs/relatórios. Uma partição sem caminho disponível aparece como UNREACHABLE;
o controlador não consegue criar conectividade que a rede física não oferece.

```sh
python -m unittest discover -s tests
```

Os testes incluem cortes, recuperação, partições, expiração de routers, ativação
por pedido, entrega multicast/upstream e ROUTE_PING/PONG com percursos de ida e
volta, timeouts e mudanças de versão através de sockets TCP reais. As sondas
são simuladas nestes testes. Para validar cortes físicos e sondas reais na VM,
consultar [o guia CORE](topology/README.md).

## Etapa 4 — vídeo multicast UDP ao vivo

A implementação adapta `VideoStream.py`, `RtpPacket.py`, `Servidor.py` e
`ClienteGUI.py` do material Python fornecido. Mantém o formato do ficheiro:
cinco bytes ASCII com o comprimento, seguidos do JPEG. Substitui a transmissão
unicast fixa do exemplo por encaminhamento nos ramos da árvore overlay.

Existe **uma única leitura e um único relógio de emissão** no servidor. Cada
fragmento é enviado uma vez por ramo; na bifurcação R3 replica para C1 e C2.
Se o caminho calculado tiver routers antes de R3, esses routers também encaminham
a mesma cópia pelo ramo partilhado. Não é criada uma fonte por cliente.

### Arranque na VM

O vídeo de teste foi colocado em `videos/movie.Mjpeg` a partir do material
fornecido. Garantir que também está presente na VM. Na VM, instalar o suporte Tk da
distribuição (`python3-tk` em Ubuntu/Debian) e criar um ambiente Python:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Reiniciar a sessão CORE para carregar a versão atual em todos os agentes. Na
raiz do projeto, em Server1:

```sh
.venv/bin/python -m src.main --controller --id Server1 --video videos/movie.Mjpeg
```

Nos terminais CORE dos clientes (com acesso ao display gráfico da VM):

```sh
# C1
.venv/bin/python -m src.main 10.0.4.20 --id C1 --watch
# C2
.venv/bin/python -m src.main 10.0.4.20 --id C2 --watch
```

Cada cliente abre uma janela e apresenta os frames completos mais recentes.
Fechar a janela termina o cliente e envia LEAVE. Sem `--watch`, o cliente recebe
e contabiliza vídeo sem janela e mantém os comandos no terminal. Os routers não
precisam de Pillow, display ou ficheiro de vídeo.

O vídeo usa **UDP 6000** por omissão (`--udp-port` permite alterar a porta local).
Os nós anunciam a porta pelo canal TCP; o servidor fornece os destinos UDP de
cada ramo. As portas TCP 5000/5001 mantêm as suas funções. O formato transmite
a stream `default`; não há uma segunda sessão RTSP paralela ao controlo existente.

Por omissão, a fonte envia a **20 FPS** e repete o vídeo, mantendo os números de
frame crescentes. `--fps 25` altera o ritmo e `--no-loop` termina a emissão no
fim do ficheiro. Sem ramos ativos, a fonte pausa a leitura e não envia dados;
um cliente que entra durante a emissão acompanha o ponto atual da mesma fonte.

### Perdas sem bloquear a reprodução

- Datagramas até **1200 bytes**, com fragmentação na aplicação e sequências
  RTP de 16 bits. O prefixo privado transporta sessão, controlador, versão da
  rota, número de frame de 64 bits, índice/total de fragmentos e TTL.
- O cabeçalho segue a base RTP v2 do exemplo, com payload type dinâmico 96.
  A fragmentação é privada: não é o formato RTP/JPEG RFC 2435 e não é diretamente
  compatível com o leitor original ou leitores RTP genéricos.
- Routers validam e encaminham fragmentos sem descodificar ou reconstruir JPEGs.
  Apenas os clientes fazem a reconstrução, limitada a um frame em curso.
- Assim que chega um frame mais recente, o anterior incompleto é descartado.
  Não há NACK, retransmissão nem espera por frames em falta.
- Um prazo de 100 ms limpa fragmentos incompletos mesmo se não chegarem novos
  pacotes; não atrasa a apresentação de um frame completo mais recente.
- A janela usa uma fila de tamanho 1 e descodifica em memória na thread gráfica,
  sem ficheiros JPEG temporários nem acumulação de frames antigos.
- As perdas são registadas apenas em ficheiro: `logs/C1-video.log`,
  `logs/C2-video.log`, etc. `--loss-log CAMINHO` escolhe outro destino.

Exemplo de perda de fragmentos e de frames totalmente ausentes:

```text
FRAME_LOSS reason=newer_frame frames=14..14 fragments=5/6
FRAME_LOSS reason=sequence_gap frames=18..20 fragments=0/0
```

Um intervalo perdido por completo é identificado quando chega a sequência
seguinte; não é possível contar frames que nunca foram observados enquanto
a ligação permanece totalmente interrompida. O primeiro frame de um cliente
que acaba de entrar define a sua referência, sem contar o passado como perda.

Mudanças de árvore descartam pacotes da versão anterior. Após convergência, a
reprodução retoma nos frames atuais, sem recuperar o vídeo perdido. Uma perda
pode deixar temporariamente a última imagem visível, mas não bloqueia a receção
nem atrasa os frames seguintes. `/ping` continua disponível no servidor para
verificar os percursos de controlo instalados.
