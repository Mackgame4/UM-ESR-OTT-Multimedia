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
