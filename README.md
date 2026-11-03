# esr-ott

Universidade do Minho ESR TP2 project: an application-level overlay network for
multimedia delivery, intended to be tested using the CORE network emulator.

The application uses only the Python standard library. Stage 1 implements an
overlay node that acts as both a TCP client and server, with persistent,
full-duplex connections to manually configured neighbours.

Transport protocol decisions:

- **TCP** for control messages, including neighbour discovery, topology updates,
  and routing information, because reliable, ordered delivery helps keep the
  overlay network state consistent.
- **UDP** for MJPEG video streaming, because it avoids retransmission delays and
  allows timely delivery of new frames when packets are lost.

UDP streaming is reserved for a later stage; this stage implements TCP control.

## Estratégia de construção do overlay

Numa primeira fase, vamos utilizar a **estratégia 1 — abordagem manual**:
os peers/nós vizinhos são indicados explicitamente no arranque da aplicação,
através da opção `--peer HOST:PORT`, repetida para cada vizinho. Esta fase não
necessita de um controlador.

Com o avanço do desenvolvimento, vamos introduzir a **estratégia 2 — abordagem
baseada num controlador**, responsável
pelo registo de novos nós e pela indicação dos respetivos vizinhos. As ligações
entre os nós e o streaming funcionarão diretamente entre os peers, sem depender
da disponibilidade contínua do controlador. Se o controlador ficar indisponível,
as ligações existentes continuam a funcionar; apenas ficam impedidos novos
registos até o controlador voltar a estar disponível.

### Estratégia 3 — bootstrap distribuído com substituição automática

Como evolução a avaliar, cada nó terá capacidade para atuar como **bootstrapper**,
participando na gestão distribuída dos registos e da informação sobre os vizinhos.
Um dos nós assume o papel de bootstrapper ativo; se esse nó falhar, outro nó
assume automaticamente esse papel. O objetivo é manter a rede em funcionamento
e permitir novos registos após a substituição, sem depender de um controlador fixo.

Esta estratégia exige partilha e atualização dos registos entre os nós, deteção
de falhas e um mecanismo de eleição do novo bootstrapper. Os novos nós devem
conhecer vários contactos de arranque para conseguirem contactar um nó disponível
quando o anterior falhar. A recuperação das ligações e dos caminhos de streaming
afetados pela falha terá de ser tratada em conjunto com a gestão do overlay.
Esta alternativa distribuída ainda não está implementada.

O principal desafio é distinguir “o bootstrapper morreu” de “não consigo comunicar com ele”. Por exemplo:
1. A é o bootstrapper.
2. B deixa de receber respostas de A e assume o papel.
3. A continua ativo e acessível a outros nós.
4. Passam a existir dois bootstrappers que podem atribuir vizinhos com informações diferentes.

### Estratégia 4 — vários bootstrappers com topologia pré-configurada

Para este projeto, a evolução recomendada é manter a estratégia 1 na fase inicial
e avançar para um serviço de bootstrap redundante. Dois ou mais bootstrappers
ficam disponíveis em simultâneo e usam a mesma versão de um ficheiro de
configuração que associa cada identificador de nó aos seus endereços e vizinhos.
A estratégia 2 pode servir como passo intermédio; a estratégia 3 mantém-se como
alternativa caso seja necessário um registo global dinâmico com líder substituível.

O funcionamento previsto é:

1. O novo nó conhece uma lista de contactos de bootstrap e contacta um deles,
   identificando-se.
2. O bootstrapper devolve os vizinhos definidos na configuração para esse nó.
3. Se o contacto falhar ou não responder dentro do tempo limite, o nó tenta outro
   bootstrapper da lista.
4. O nó estabelece ligações TCP diretamente com os vizinhos e mantém localmente
   o estado dessas ligações. O streaming utiliza UDP entre os nós.

Não existe um bootstrapper principal nem eleição: qualquer réplica pode responder
à mesma consulta. O serviço fornece configuração; não mantém um registo global
autoritativo dos nós ativos nem reserva recursos. Assim, não é necessário
sincronizar registos dinâmicos entre bootstrappers. Enquanto existir um contacto
de bootstrap acessível com a configuração correta, os nós previamente configurados
podem obter vizinhos mesmo que outro bootstrapper falhe. As ligações existentes
continuam independentes do serviço de bootstrap.

Esta opção exige distribuir a mesma versão da configuração por todos os
bootstrappers. Acrescentar um identificador de nó novo exige atualizar essa
configuração; a lista devolvida também pode conter vizinhos desligados. A deteção
de falhas, as novas tentativas de ligação e a recuperação dos caminhos de vídeo
são responsabilidades separadas da gestão do overlay e do encaminhamento.
Se todos os contactos de bootstrap ficarem inacessíveis, novos nós sem configuração
local não conseguem obter vizinhos. Esta estratégia ainda não está implementada.

  -- Fun thing:

  in case an overlay node is running on a machine/router and it dies we can know the parent node and if still alive connect directly (this in overlay), if a node fails in the underlay we need another connection route

Run two terminals from the project root (Python 3):

```sh
# Terminal A: listen on the default TCP port 5000
python -m src.main --id A

# Terminal B: listen on port 5001 and connect to A
python -m src.main --id B --port 5001 --peer 127.0.0.1:5000
```

Both terminals should report `RX HELLO` from the other node. Each endpoint
automatically sends one HELLO when a connection opens. The same TCP connection
supports sending and receiving concurrently; there is no request/reply loop.

Interactive commands:

- `/hello`: send HELLO to every connected neighbour.
- Any plain text: send a MESSAGE to every connected neighbour.
- `/peers`: list connected neighbours (the remote socket address is shown;
  incoming connections show the client's ephemeral source port).
- `/connect HOST:PORT`: connect to another neighbour while running.
- `/quit` or Ctrl+C: close connections and stop. End of standard input also stops
  the application, so keep a terminal open for interactive use.

Use repeated `--peer HOST:PORT` options for multiple initial neighbours. The
listener binds to `0.0.0.0` by default; `--host` selects a local IPv4 address.
Connections are direct neighbour links: automatic discovery, forwarding,
reconnection, routing and video transmission are not implemented yet.

Control messages are UTF-8 JSON objects terminated by a newline, with `type`
(`HELLO` or `MESSAGE`), `node_id`, and `text` fields. The maximum encoded message
size, including its newline, is 65536 bytes. Invalid or oversized incoming
messages close that connection while the node continues accepting neighbours.

See [the topology guide](topology/README.md) for a three-node test and CORE setup.

Run the tests:

```sh
python -m unittest discover -s tests
```
