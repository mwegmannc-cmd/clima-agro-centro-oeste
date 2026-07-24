# Clima x Agro — Centro-Oeste (soja/milho)

Dashboard interativo que monitora clima e chuva em cinco polos produtores de
soja/milho do Centro-Oeste — **Sorriso (MT)**, **Rondonópolis (MT)**,
**Primavera do Leste (MT)**, **Rio Verde (GO)** e **Dourados (MS)** —
comparando com o mesmo período do ano passado e com a média histórica dos
últimos 5 anos, para apoiar leitura de janela de plantio e condições de
lavoura.

Dados: [Open-Meteo](https://open-meteo.com) — API pública, gratuita, sem
necessidade de chave/token.

## Como executar localmente

1. (Recomendado) crie um ambiente virtual:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows
   # source .venv/bin/activate   # macOS/Linux
   ```

2. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

3. Rode o app:

   ```bash
   streamlit run app.py
   ```

4. O navegador abrirá automaticamente em `http://localhost:8501`.

## O que o app mostra

- **Seletor de polo produtor**: 5 municípios-chave do agronegócio no MT/GO/MS.
- **Previsão dos próximos 7 dias**: temperatura máx/mín e condição de chuva.
- **Chuva acumulada (30 dias observados)**: comparada ao mesmo período do ano
  passado e à média histórica de 5 anos — indicador direto de umidade do
  solo / condição para plantio.
- **Chuva prevista (7 dias)**: acumulado futuro vs. mesmo período do ano
  passado.
- **Veranico (dry spell)**: dias consecutivos sem chuva relevante (< 1 mm),
  com projeção de quanto pode se estender caso a previsão se confirme. O
  alerta textual só soa em fases sensíveis do calendário agrícola (plantio,
  desenvolvimento) — na entressafra (estação seca), o mesmo veranico é
  tratado como normal.
- **Painel de Insights Agro**: cards com os desvios acima e um card textual
  combinando os indicadores em uma leitura agronômica (ex.: "chuva 30%
  abaixo do ano passado durante o plantio da soja — possível atraso na
  janela").
- **Gráficos**: chuva acumulada (curva, este ano x ano passado x média),
  temperatura (linha) e chuva diária (barras), todos com uma linha vertical
  marcando "hoje" (fronteira entre dados observados e previsão).

## Notas técnicas

- Uma única chamada à API de previsão (`api.open-meteo.com`) traz os últimos
  30 dias observados + 7 dias de previsão via o parâmetro `past_days`.
- Ano passado e média histórica vêm da API de arquivo
  (`archive-api.open-meteo.com`), com dados reais observados.
- Cache de 1h (dados atuais) e 6h (histórico) via `st.cache_data`.
- As fases do calendário agrícola (plantio, desenvolvimento, colheita,
  entressafra) são aproximações indicativas por mês — não substituem
  recomendação agronômica local, que varia por cultivar, latitude e safra.
- Botão "Atualizar dados" na barra lateral limpa o cache manualmente.
