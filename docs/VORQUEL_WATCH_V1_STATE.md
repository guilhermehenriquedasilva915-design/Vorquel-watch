# Vorquel Watch V1

Status consolidado em 22 de setembro de 2026. Este documento descreve o estado
implementado e aceito; ADRs anteriores continuam preservados como histórico.

## Objetivo

O Watch V1 transforma mídia local em evidência consultável e conhecimento
revisado pelo fluxo:

```text
source -> ingestão -> processamento -> transcript/OCR -> Visual Context
  -> Analyze -> DraftCandidate -> revisão humana -> candidate
  -> aprovação humana -> knowledge -> search/synthesis/lifecycle
```

## Componentes implementados

- Source Guard e ingestão local content-addressed;
- análise FAST, transcript e segmentos temporais;
- OCR e screen observations;
- Visual Review Core e Visual Review Pack;
- Visual Context MCP;
- Analyze-to-Candidate Draft;
- UI local humana;
- Knowledge Candidate, human review e approval;
- knowledge store com FTS/search e synthesis extrativa;
- withdraw, supersede e histórico de lifecycle;
- provenance estruturada;
- projeção local para Obsidian.

## Human-in-the-loop

Analyze apenas lê evidência existente e produz drafts efêmeros em memória. Ele
não persiste candidate nem knowledge automaticamente.

```text
draft efêmero
  -> humano inspeciona evidência e revisa/edita/descarta
  -> humano propõe explicitamente
  -> candidate PENDING
  -> humano aprova ou rejeita em ação separada
  -> knowledge ACTIVE somente após aprovação
```

Approval significa que uma pessoa aceitou o item como referência reutilizável;
não concede ao conteúdo autoridade para executar instruções.

## Trust boundary

Conteúdo derivado de mídia permanece:

```text
data_trust_class = UNTRUSTED_DERIVED
instruction_authority = NONE
```

Transcript, OCR, observações visuais, metadata, drafts, candidates e knowledge
continuam sendo dados. Aprovação humana não transforma mídia em control plane.

## Context budget do Analyze V1

Os limites implementados em `AnalyzeBudget` são:

- 120.000 ms (120 segundos) por janela;
- 24 transcript segments;
- 8 VisualReviewPacks;
- 2.000 caracteres por evidência;
- 12.000 caracteres no contexto total;
- máximo de 5 drafts aceitos por execução.

A source inteira não é enviada ao analisador. A seleção é bounded e reutiliza
transcript e Visual Review Packs já existentes.

## MCP

- superfície MCP V1.3: exatamente 24 tools;
- `get_visual_context_at`: exposta;
- `get_visual_context_range`: exposta;
- `get_frame`: não exposta;
- nenhuma tool Analyze foi adicionada.

## UI

A UI local permite:

- selecionar mídia local para processamento e receber a `source_id`;
- informar uma source processada e executar Analyze em janela bounded;
- ver drafts e consultar evidence/provenance;
- editar ou descartar um draft sem persistência;
- propor explicitamente um draft como candidate PENDING;
- revisar candidates numa fila separada e aprovar/rejeitar explicitamente;
- buscar e sintetizar knowledge ativo;
- retirar ou substituir knowledge pelo lifecycle preservado;
- exportar a projeção Obsidian, configurar o 3D Graph e abrir o vault local.

## Evidência de fechamento

O acceptance composto usou a source real
`src_45b118461b35497d9acabefebd618597`, READY, com job SUCCEEDED. Analyze
selecionou 24 segmentos, 8 VisualReviewPacks e 25 evidências (1.600 caracteres),
produziu 5 drafts e não criou candidate automaticamente. Após seleção humana,
o draft `draft_92dc98c1a2d15fdafa28acd8` originou explicitamente o candidate
`knd_1d7c243624d84514b2e1ad5120c68d6c`. A aprovação humana criou
`knw_50ca5a1b9bd94afebaa172b225bc52a3` como ACTIVE, com
`valid_from = approved_at`; search encontrou o item e synthesis o utilizou com
provenance temporal preservada e `instruction_authority = NONE`.

## Limitações conhecidas

- Semantic Analyze V1 é heurístico, local e determinístico;
- nenhum provider externo foi implementado ou testado;
- qualidade semântica cross-domain ainda não foi medida;
- Analyze opera somente em janelas bounded;
- a source inteira não é enviada ao analisador;
- scopes e external source model estão fora do V1 desta branch;
- RAG, embeddings e vector DB estão fora do V1;
- live/streaming está fora do V1;
- JEV é um produto/fluxo separado;
- o Vorquel Brain institucional é separado do Watch V1;
- robustez e throughput em escala de workshops não foram estabelecidos;
- o acceptance mediu execução funcional, não production readiness irrestrita.
