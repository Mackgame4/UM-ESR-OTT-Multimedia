# Etapa 1 — construção da topologia overlay

O controlo usa TCP, por omissão na porta **5000**. Cada processo é simultaneamente
servidor e cliente: aceita vários vizinhos e pode iniciar ligações enquanto recebe
mensagens. O streaming de vídeo usará UDP numa etapa posterior.

## Teste local com três nós

Abrir três terminais na raiz do projeto, pela ordem indicada:

```sh
# Terminal A
python -m src.main --id A --port 5000

# Terminal B
python -m src.main --id B --port 5001 --peer 127.0.0.1:5000

# Terminal C
python -m src.main --id C --port 5002 --peer 127.0.0.1:5001
```

A overlay tem as ligações `A <-> B <-> C`. Cada ligação usa um único socket TCP
full-duplex. B recebe HELLO de A e C; A e C recebem HELLO de B.

1. Escrever `/peers` em B: devem aparecer A e C.
2. Escrever `/hello` em B: A e C devem mostrar `RX HELLO de B: HELLO`.
3. Escrever texto em A e C, mesmo enquanto B envia mensagens: B recebe as duas
   mensagens; A e C recebem as mensagens enviadas por B.
4. Em C, executar `/connect 127.0.0.1:5000` para acrescentar uma ligação direta a A.
5. Executar `/quit` em C: os restantes nós continuam ativos e aceitam ligações.

As mensagens vão apenas para vizinhos diretos; B ainda não reencaminha mensagens
entre A e C. Configurar cada ligação numa única extremidade evita sockets
duplicados: ambos os lados já conseguem enviar e receber no mesmo socket.

## Teste no CORE

Criar três nós com conectividade IP entre os vizinhos desejados. O caminho do
projeto e o Python 3 devem estar acessíveis em cada terminal CORE. Confirmar a
conectividade com `ping` antes de iniciar a aplicação.

Por exemplo, se A for acessível a B em `10.0.0.1` e B for acessível a C em
`10.0.1.1`, executar na raiz do projeto de cada nó:

```sh
# Nó A
python3 -m src.main --id A

# Nó B
python3 -m src.main --id B --peer 10.0.0.1:5000

# Nó C
python3 -m src.main --id C --peer 10.0.1.1:5000
```

Substituir os endereços pelos IPs reais da topologia. Como os nós têm endereços IP
distintos, todos podem usar a porta 5000. Repetir os testes de HELLO e texto acima.
Este diretório contém as instruções; ainda não inclui uma sessão CORE guardada.
