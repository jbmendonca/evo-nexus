# EvoNexus Pau Brasil - Registro Operacional

Documento de referência para manutenção e continuidade técnica do ambiente Pau Brasil.

## Infraestrutura ativa

- VPS: `72.60.10.135`
- App path: `/opt/evo-nexus`
- Compose principal:
  - `docker-compose.yml`
  - `docker-compose.override.yml`
- Containers críticos:
  - `evonexus-dashboard`
  - `evonexus-ciss-postgres`
  - `evonexus-ciss-dashboard`

## Estado funcional atual

- Frontend principal no ar.
- Rotas de negócio no ar:
  - `/painel-vendas`
  - `/painel-clientes`
  - `/ciss-analytics`
- Integração CISS autenticando corretamente.
- Painel de clientes restaurado com métricas materializadas.

## Ajustes recentes aplicados

1. Restauro do vínculo cliente-venda:
   - Enriquecimento via cabeçalho `get_pedido_venda`.
   - Propagação de `id_cliente` para itens.
2. Materialização de métricas:
   - `dw_cliente_metricas`
   - `dw_cliente_produto_metricas`
3. Robustez ETL:
   - Fallback de campo de data na extração de `get_pedido_venda_prod`.
4. UX menu:
   - Rótulos alterados para `Painel de Venda` e `Painel de Clientes`.

## Evidências de restauração

- `dw_vendas` com cliente: 13.479
- `dw_itens_venda` com cliente: 41.203
- `dw_cliente_metricas`: 2.205
- `dw_cliente_produto_metricas`: 22.508

## Observações

- Endpoints `/api/*` exigem autenticação e retornam `401` sem sessão, comportamento esperado.
- Em restart de container, pode ocorrer breve `connection reset` até health ficar `healthy`.

