# Estratégia 2 — testar na VM CORE

## Arranque

1. Reiniciar a sessão para limpar processos de versões anteriores.
2. Abrir `topology.xml`, iniciar a emulação e esperar pela convergência IP.
3. Confirmar `ping -c 3 10.0.4.20` em C1. A VM precisa de `ping` (iputils),
   Python 3 e `vcmd`, e o projeto deve estar acessível nos nós.
4. Na raiz do projeto, em Server1:

   ```sh
   python3 -m src.main --controller --id Server1
   ```

5. O servidor inicia agentes de controlo nos routers. `/members` deve mostrar
   os routers registados como CONTROL ONLY enquanto não há pedidos de clientes.
6. Em C1 e C2, respetivamente:

   ```sh
   python3 -m src.main 10.0.4.20 --id C1
   python3 -m src.main 10.0.4.20 --id C2
   ```

7. Cada cliente regista-se e pede a stream `default`. Em Server1, `/routes` deve
   mostrar os caminhos ACTIVE, o número de saltos e a versão. O caminho depende
   dos relatórios recebidos e pode mudar durante o arranque dos agentes.
8. Escrever texto em Server1: C1 e C2 recebem a mensagem com origem Server1.
   Escrever texto em C1: apenas Server1 recebe, sem entrega a C2.

Se a identificação automática da sessão falhar, acrescentar ao comando do
servidor `--core-session /tmp/pycore.12345` com o diretório real. Os logs de cada
agente ficam nessa sessão, por exemplo `R3-ott.log`. O lançamento usa a interface
[vcmd do CORE](https://coreemu.github.io/core/devguide.html).

## Cortar uma ligação e recuperar

1. Consultar `/routes` e escolher uma ligação router-router usada no caminho
   de C1 que tenha alternativa na rede. Não cortar a única ligação de C1 a S1,
   pois nesse caso C1 fica fisicamente isolado.
2. Desativar essa ligação na sessão CORE em execução. Não editar o XML guardado.
3. Esperar algumas rondas de monitorização (intervalo 2 s mais tempo de sondas,
   convergência IP e instalação das rotas). Consultar `/routes` novamente.
4. A versão deve mudar e o caminho deve evitar a ligação cortada. Se não existir
   alternativa, o estado é UNREACHABLE e não é mantido um caminho obsoleto.
5. Enviar nova mensagem em Server1: os clientes alcançáveis devem recebê-la.
   Verificar também uma mensagem de C1 para Server1.
6. Restaurar a ligação. Quando ambos os extremos confirmarem disponibilidade,
   o controlador volta a considerar esse caminho e escolhe o menor número de saltos.

A sonda usa `ping -I INTERFACE -t 1`, portanto a recuperação do OSPF por outro
percurso não mascara a falha da adjacência direta. As mensagens para o bootstrapper
continuam a precisar de conectividade IP; uma partição pode impedir os relatórios
até a rede subjacente recuperar. Durante a convergência pode haver perda de dados.

## Ativação e abandono

- `/unsubscribe` em C1 retira o seu ramo de dados; C2 continua a receber.
- `/unsubscribe` em C2 deixa a árvore vazia; os agentes continuam CONTROL ONLY.
- `/subscribe` volta a pedir a stream e permite reconstruir a árvore.
- `/quit` envia LEAVE; um processo morto abruptamente é removido por lease após
  15 s desde a última renovação. Routers são relançados pelo servidor se o processo
  que este iniciou tiver terminado.
- Não existe bootstrapper num router: o controlador é sempre o servidor escolhido.

## Etapa 3 — verificar os percursos

1. Iniciar Server1, C1 e C2 como indicado acima e esperar por rotas ACTIVE.
2. Executar `/ping` em Server1. Deve surgir um resultado OK por cliente, com
   caminho acumulado, RTT, ID do teste e versão da árvore. O prazo é de 3 segundos.
3. Comparar os caminhos com `/routes`. Inspecionar `R3-ott.log` e os logs dos
   outros routers do percurso: devem mostrar ROUTE_PING na ida e ROUTE_PONG
   no retorno, com os mesmos identificadores de teste.
4. Cortar uma ligação com alternativa, esperar pelo recálculo e repetir `/ping`.
   O novo percurso observado deve coincidir com o novo caminho de `/routes`.
5. Se a rota mudar durante o teste, o resultado é STALE. Repetir depois de
   estabilizar. Sem caminho, surge UNREACHABLE; sem resposta no prazo, TIMEOUT.

O RTT não muda a métrica BFS. `/ping` testa o overlay completo; `/hello` testa
apenas vizinhos e o `ping` Linux continua a ser usado nas sondas de adjacência.

## Etapa 4 — vídeo multicast UDP

Copiar `videos/movie.Mjpeg` para a VM, instalar Pillow (`pip install -r requirements.txt`)
no ambiente Python escolhido e instalar o suporte Tk da distribuição. O terminal
CORE do cliente deve ter acesso ao display gráfico da VM. Reiniciar a sessão
para limpar os agentes de versões anteriores.

Em Server1, na raiz do projeto:

```sh
python3 -m src.main --controller --id Server1 --video videos/movie.Mjpeg
```

Nos terminais de C1 e C2, respetivamente:

```sh
python3 -m src.main 10.0.4.20 --id C1 --watch
python3 -m src.main 10.0.4.20 --id C2 --watch
```

Se as dependências estiverem numa venv, substituir `python3` pelo seu executável,
por exemplo `.venv/bin/python`. Sem display, omitir `--watch` para testar a receção
sem janela. O vídeo usa UDP 6000; verificar que essa porta está disponível nos nós.

O servidor lê o vídeo uma vez e partilha a mesma emissão por todos os clientes.
Os routers encaminham fragmentos por ramo, replicando apenas nas bifurcações.
`/routes` mostra o percurso efetivamente instalado, que pode incluir routers
intermédios antes de R3. O vídeo é repetido a 20 FPS por omissão.

Para testar perdas, aplicar perda/atraso a uma ligação no CORE em execução:
o cliente deve continuar nos frames completos mais recentes. Inspecionar
`logs/C1-video.log` e `logs/C2-video.log`: as entradas FRAME_LOSS identificam
frames incompletos e intervalos de sequência perdidos. Não há retransmissões.
O primeiro frame reconstruído gera VIDEO_START no mesmo ficheiro.

Para testar recuperação, cortar um ramo com alternativa, esperar pela mudança
de `/routes` e verificar que o vídeo retoma sem reproduzir o intervalo perdido.
Os relatórios de perdas não aparecem repetidamente no terminal, apenas no log.
Fechar a janela termina o cliente e remove a sua subscrição.

## Teste automatizado opcional

No terminal do host Linux da VM, fora dos terminais dos nós:

```sh
sudo python3 tests/core_smoke.py
```

Este teste cria namespaces CORE isolados com todos os dispositivos e ligações
iniciais do XML. Usa rotas IP estáticas, testa agentes, pedidos de rota, multicast,
uplink isolado, `/ping` para C1/C2 e LEAVE. Se `videos/movie.Mjpeg` estiver presente,
também verifica a reconstrução de vídeo UDP nos dois clientes sem janela.
Não testa cortes físicos nem convergência OSPF; esses
casos são verificados pelo procedimento manual acima. Os testes de recálculo e
recuperação automáticos com sondas simuladas estão em `tests/test_live_routes.py`.

O script remove os namespaces/interfaces que criou e conserva os logs no
diretório indicado. Não altera uma sessão CORE existente. Deve ser executado
na VM, não no ambiente Windows do repositório.
