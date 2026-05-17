# AGENTES.MD

Contexto operacional para agentes que trabalham no repositório `D:\Devops\EvoNexusPauBrasil\evo-nexus`.
Foco do negócio: Pau Brasil Materiais de Construção.

---

## Estado Atual

Atualizado em 2026-05-17.

- Branch ativa: `feat/openrouter-smart-router`
- VPS principal atual: `72.60.10.135`
- Caminho de deploy: `/opt/evo-nexus`
- Stack: Docker Compose (`docker-compose.yml` + `docker-compose.override.yml`)
- Serviço principal: `evonexus-dashboard`
- Serviços CISS: `evonexus-ciss-postgres`, `evonexus-ciss-dashboard`

### Situação validada

- Painel de Venda: OK (`/painel-vendas`)
- Painel de Clientes: OK (`/painel-clientes`)
- Dashboard CISS: OK (`/ciss-analytics`)
- Integração Integrim: OK (health de autenticação validado)

### Restauração executada

- Reprocessado vínculo cliente-venda via `get_pedido_venda`.
- Propagado `id_cliente` para itens de venda.
- Materializadas métricas de cliente e cliente x produto.
- Ajustado fallback da extração de vendas para reduzir falhas de filtro de data.
- Atualizados rótulos de menu para:
  - `Painel de Venda`
  - `Painel de Clientes`

### Métricas restauradas no DW (última validação)

- `dw_vendas` com cliente: 13.479
- `dw_itens_venda` com cliente: 41.203
- `dw_cliente_metricas`: 2.205
- `dw_cliente_produto_metricas`: 22.508

---

## Regras Operacionais

- Não sobrescrever `docker-compose.override.yml` sem revisar labels/rede.
- Não remover serviços CISS do compose sem plano de migração.
- Sempre validar após deploy:
  1. `docker inspect evonexus-dashboard` (status/health)
  2. `GET /painel-vendas`, `GET /painel-clientes`, `GET /ciss-analytics`
  3. Contagens no Postgres CISS

---

## Comandos de Verificação Rápida

- `docker compose -f docker-compose.yml -f docker-compose.override.yml ps`
- `curl -I http://127.0.0.1:8081/painel-vendas`
- `curl -I http://127.0.0.1:8081/painel-clientes`
- `docker exec evonexus-ciss-postgres psql -U ciss_dw -d ciss_dw -c "SELECT COUNT(*) FROM dw_cliente_metricas;"`

