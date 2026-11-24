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

## Teste automatizado opcional

No terminal do host Linux da VM, fora dos terminais dos nós:

```sh
sudo python3 tests/core_smoke.py
```

Este teste cria namespaces CORE isolados com todos os dispositivos e ligações
iniciais do XML. Usa rotas IP estáticas, testa agentes, pedidos de rota, multicast,
uplink isolado e LEAVE. Não testa cortes físicos nem convergência OSPF; esses
casos são verificados pelo procedimento manual acima. Os testes de recálculo e
recuperação automáticos com sondas simuladas estão em `tests/test_live_routes.py`.

O script remove os namespaces/interfaces que criou e conserva os logs no
diretório indicado. Não altera uma sessão CORE existente. Deve ser executado
na VM, não no ambiente Windows do repositório.
