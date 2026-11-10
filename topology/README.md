# Testar o bootstrap no CORE da VM

`topology.xml` descreve a rede física. Server1 calcula o overlay a partir das
ligações existentes; não é necessário escrever uma configuração overlay.

## Teste manual

Se a sessão estava a executar a versão anterior, reiniciá-la primeiro para
remover os processos antigos de R9/R7/R4 e carregar a versão nova nos nós.

1. Abrir `topology.xml` no CORE e iniciar a sessão. Esperar pela convergência IP.
2. Em C1, verificar `ping -c 3 10.0.4.20` (Server1).
3. Em Server1, na raiz do projeto, executar:

   ```sh
   python3 -m src.main --controller --id Server1
   ```

4. Em C1, na raiz do projeto, executar manualmente:

   ```sh
   python3 -m src.main 10.0.4.20 --id C1
   ```

5. Server1 deve mostrar REGISTER C1 e o caminho
   `Server1 -> R3 -> C1`. Apenas R3 inicia a aplicação automaticamente;
   R4, R7 e R9 continuam a encaminhar IP sem aplicação overlay.
6. Após alguns segundos, `/peers` em C1 deve mostrar R3. Em Server1, `/peers`
   deve mostrar R3. `/members` deve mostrar `C1 (OVERLAY CLIENT)`,
   `R3 (OVERLAY ROUTER)` e os restantes routers como `UNDERLAY ONLY`.
7. Escrever texto em C1: Server1 deve mostrar `RX MESSAGE de C1`.
8. Executar `python3 -m src.main 10.0.4.20 --id C2` em C2: reutiliza os routers
   ativos e liga-se a R3.
   Escrever texto em Server1: C1 e C2 devem mostrar `RX MESSAGE de Server1`.
   Escrever texto em C1: apenas Server1 recebe; C2 não deve receber essa mensagem.
   `R3-ott.log` mostra o encaminhamento, preservando a origem de cada mensagem.
9. Sair de C1 com `/quit`: Server1 mostra LEAVE C1 e C2 continua ligado.
10. Terminar C2 com `kill -9 PID`: após 15 segundos desde a última renovação,
    C2 deixa de aparecer em `/members`.

Se a identificação automática da sessão falhar, acrescentar ao comando do
servidor `--core-session /tmp/pycore.12345`, usando o diretório real da VM.
Confirmar que contém os sockets Server1, R3, etc. e que `vcmd` está disponível.
Os logs dos routers ficam nesse diretório.

O projeto deve estar no filesystem partilhado dos nós. O lançamento remoto usa
a interface [vcmd documentada pelo CORE](https://coreemu.github.io/core/devguide.html).

## Teste automatizado opcional na VM

No terminal do host Linux da VM, fora dos terminais dos nós:

```sh
sudo python3 tests/core_smoke.py
```

Requer `vnoded`, `vcmd` e `ip`. Cria namespaces CORE temporários com todos os
dispositivos e ligações do XML. Usa rotas estáticas para não depender da
instalação OSPF; não verifica a convergência OSPF da sessão gráfica.

O script arranca Server1, confirma que R3 ainda não executa a aplicação, arranca
C1 e C2, verifica que apenas R3 foi ativado, testa multicast do servidor para os
dois clientes, testa o envio de C1 apenas para Server1 e verifica LEAVE.
Remove os namespaces e interfaces que criou e conserva os logs no diretório
indicado. Não altera uma sessão CORE existente. Este teste deve ser executado
na VM; os testes unitários normais não precisam de CORE.
