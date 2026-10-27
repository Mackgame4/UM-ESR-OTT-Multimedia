# esr-ott

Universidade do Minho ESR TP2 project: an application-level overlay network for
multimedia delivery, intended to be tested using the CORE network emulator.

This initial structure separates networking, protocol, overlay management,
routing, streaming, and monitoring for future implementation. The project
initially uses only the Python standard library.

Transport protocol decisions for future implementation:

- **TCP** for control messages, including neighbour discovery, topology updates,
  and routing information, because reliable, ordered delivery helps keep the
  overlay network state consistent.
- **UDP** for MJPEG video streaming, because it avoids retransmission delays and
  allows timely delivery of new frames when packets are lost.



  -- Fun thing:

  in case an overlay node is running on a machine/router and it dies we can know the parent node and if still alive connect directly (this in overlay), if a node fails in the underlay we need another connection route

Run the application from the project root:

```sh
python -m src.main
```

Run the tests:

```sh
python -m unittest discover -s tests
```
