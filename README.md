# GeoDoc SIG — Módulo 01: Planta técnica → camadas SIG

Aplicação local que lê uma planta (PDF, JPG ou PNG), extrai feições candidatas (perímetro, quadras, lotes), mostra as
evidências, deixa você revisar (aceitar, corrigir, rejeitar) e exporta as camadas aceitas em GeoJSON, KML e Shapefile ZIP.

- **Backend:** Python + FastAPI (`backend/`), que também serve a interface.
- **Interface:** HTML/CSS/JS puro (`frontend/`), sem build.

## Requisitos

- Windows com **Python 3.10 ou superior** no PATH (`python --version`).
- Um navegador atual (Chrome, Edge ou Firefox).
- Cerca de 4 GB de RAM livres para o PDF A0 de exemplo.

## Executar (jeito mais simples)

1. Dê duplo clique em **`iniciar.bat`**, na pasta do projeto.
2. Na primeira vez, ele cria o ambiente (`.venv`) e instala as dependências. Isso pode levar alguns minutos.
3. O navegador abre em **http://127.0.0.1:8000**.
4. Deixe a janela preta aberta enquanto usa o sistema. Para encerrar, feche a janela ou aperte `Ctrl+C`.

## Executar pelo terminal

Na pasta do projeto (PowerShell):

```powershell
# só na primeira vez
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# iniciar
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Abra **http://127.0.0.1:8000**.

## Roteiro de teste

1. Em **Abrir exemplo…**, escolha um dos arquivos de `exemplos_plantas/`:

   | Arquivo | O que esperar |
   |---|---|
   | `Exmplo01.jpg` | 1 perímetro reconstruído da tabela de 4 vértices (SIRGAS 2000 geográfico). |
   | `Exmeplo02.jpg` | 1 perímetro a partir de 34 vértices em UTM 23S. A imagem é de baixa resolução: confira a tabela. |
   | `Projeto Urbanistico (A0)_877 (1).pdf` | Cerca de 486 lotes, 19 partes de quadra e o perímetro. |

   **Tempo de extração do PDF A0:** a primeira extração leva cerca de 2 a 3 minutos (três processos em paralelo).
   O resultado fica em `.cache/` e as aberturas seguintes levam alguns segundos.

2. Clique em um contorno na **planta** ou no **mapa**. A seleção é sincronizada e o painel **Evidências** mostra
   a origem (por exemplo, a linha da tabela de áreas usada para conferir o lote).
3. Em **Revisar feição selecionada**:
   - **Aceitar**: se houver pendências, é preciso marcar a confirmação.
   - **Corrigir**: edite rótulo, categoria e vértices, e escreva o motivo. A planta e o mapa atualizam e o **Histórico** registra a alteração.
   - **Rejeitar**: a feição sai da exportação e continua no histórico.
4. Em **Exportar camadas revisadas**, baixe **GeoJSON**, **KML** ou **Shapefile ZIP**.
   Só saem feições **aceitas**, **georreferenciadas** e de camadas **visíveis** (marcadas em *Camadas detectadas*).
5. Outras ações:
   - **Enviar planta**: use seu próprio arquivo (PDF de até 20 páginas, JPG ou PNG, até 50 MB). Depois do diagnóstico, clique em **Extrair feições**.
   - **Vetorizar**: clique os vértices na planta e depois em Concluir.
   - **Georreferenciar**: informe pelo menos 4 pontos de controle e veja método e resíduos.
   - **Tabela**: reconstrua um perímetro a partir de uma tabela de vértices.
   - **Diagnóstico e fontes**: dados do arquivo, georreferenciamento, candidatos rejeitados e links para texto e vetores brutos.
   - Use a roda do mouse para dar zoom. Em zoom alto, a planta em PDF é renderizada de novo em alta resolução.

Uma feição sem georreferenciamento comprovado permanece em coordenadas da página e **não** é exportada.

## Testes automatizados

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # pytest e playwright, só para desenvolvimento
.venv\Scripts\python.exe -m pytest tests -q
```

Cobrem reconstrução de polígono, CRS ausente, geometria inválida, divergência de área, resíduos do georreferenciamento,
a tabela de áreas do PDF, o fluxo completo pela API e a exportação. Levam cerca de 30 segundos (mais na primeira vez
com o PDF A0, que ainda não está em cache).

## Agente de IA (opcional)

Sem chave, tudo funciona com o motor SIG e a revisão manual, e o botão **Interpretar página com IA** só avisa que não há chave.
Para habilitar (a chave fica só no backend), defina antes de iniciar:

```powershell
$env:AI_API_KEY  = "sua-chave"
$env:AI_MODEL    = "gpt-4.1-mini"                  # opcional
$env:AI_BASE_URL = "https://api.openai.com/v1"     # opcional (qualquer API compatível)
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

A IA só devolve **observações** validadas (página, posição, valor, confiança e motivo). Nenhuma coordenada dela vira geometria.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| A página não abre | Confirme que a janela do servidor está aberta e use `http://127.0.0.1:8000` (não `localhost:5173`). |
| A interface está desatualizada | `Ctrl+F5` para limpar o cache do navegador. |
| `Porta 8000 em uso` | Feche o servidor anterior ou use `--port 8001` e abra `http://127.0.0.1:8001`. |
| Erro ao instalar dependências | Use Python 3.10 a 3.12 de 64 bits e rode `pip install -r requirements.txt` de novo. |
| Extração do PDF muito lenta ou com pouca memória | Feche outros programas. A primeira execução é a mais lenta. |
| Quero refazer a extração do zero | Apague a pasta `.cache/` e a pasta `data/` (projetos gerados) e abra o exemplo de novo. |

## Estrutura

```
backend/            API, motor SIG, extração, exportação, agente de IA
  pdf_lots.py       segmentação de lotes, tabela de áreas e perímetro do PDF A0
  extraction.py     perfis dos exemplos e montagem das feições
  geometry.py       polígonos, CRS, transformação afim, resíduos, área
frontend/           interface (index.html, style.css, app.js)
exemplos_plantas/   PDF e JPGs de exemplo
tests/              testes automatizados
scripts/            captura de telas (Playwright) e servidor de desenvolvimento opcional
iniciar.bat         inicia tudo com duplo clique
```

## Plantas Macuco

Os dois PDFs fornecidos são reconhecidos pelo SHA-256, inclusive quando enviados com outro nome.
Os textos estão convertidos em desenhos; a tabela E/N foi transcrita visualmente e vinculada
ao conteúdo exato de cada arquivo. Não se trata de OCR genérico para qualquer planta.

| Arquivo | Divisões | Pontos | Linhas de limite | Outras feições |
|---|---:|---:|---:|---|
| `macuco_divisao1.PDF` | 3 | 29 | 31 | Perímetro e corpo d’água (polígonos) |
| `macuco_divisão2.PDF` | 13 | 36 | 48 | Perímetro (polígono) e curso d’água (linha) |

O georreferenciamento usa **SIRGAS 2000 / UTM 24S (EPSG:31984)**, conforme o datum,
o meridiano central e o hemisfério indicados nos documentos. O motor associa todos os
vértices do perímetro à tabela, exige resíduo máximo de 5 cm e confere a área declarada.
As divisões vêm dos caminhos vetoriais originais; sua cobertura do perímetro é conferida.
Pontos, linhas e polígonos podem ser revisados e exportados em GeoJSON, KML ou Shapefile.

**Pendência dos documentos:** a latitude/longitude de P1 no carimbo diverge cerca de 27 km
de P1 na tabela. A posição adotada é a da tabela completa, que confere com o desenho e a
área. Essa divergência aparece nas pendências de todas as feições e precisa de confirmação
pelo responsável pelo levantamento. Resíduos pequenos do ajuste não comprovam acurácia externa.

Para usar, reinicie a aplicação, atualize o navegador e abra cada PDF em **Abrir exemplo…**,
ou envie o arquivo e clique em **Extrair feições**. Revise as candidatas antes de exportar.

## Limites conhecidos

- A extração automática de lotes foi calibrada para o PDF A0 de exemplo. Em outra planta, use o diagnóstico, a vetorização e o georreferenciamento assistidos.
- Nos JPGs de exemplo, os vértices vêm de transcrição humana ligada ao hash do arquivo, e não de OCR.
- Vias e construções são reconhecidas, mas ainda não viram feições.
- Confiança alta significa "área confere com a tabela", e não "contorno correto": confira visualmente antes de aceitar.
