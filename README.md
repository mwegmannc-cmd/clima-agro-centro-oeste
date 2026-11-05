# Clima x Agro — Centro-Oeste (soja/milho)

Dashboard interativo que monitora clima e chuva em cinco polos produtores de
soja/milho do Centro-Oeste — **Sorriso (MT)**, **Rondonópolis (MT)**,
**Primavera do Leste (MT)**, **Rio Verde (GO)** e **Dourados (MS)** —
comparando com o mesmo período do ano passado e com a média histórica dos
últimos 10 anos, para apoiar a leitura de janela de plantio e condições de
lavoura.

Dados: [Open-Meteo](https://open-meteo.com) — API pública, gratuita, sem
necessidade de chave/token.

## Como executar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

O navegador abre em `http://localhost:8501`.

## O que o app mostra

- **Semáforo de insights** (🟢 / 🟡 / 🔴) com leitura textual combinando os indicadores.
- **Chuva acumulada** na janela retroativa (15, 30, 60 ou 90 dias) vs. ano
  passado e média de 10 anos, com **ranking** do ano na série histórica.
- **Janela de plantio**: chuva dos últimos 15 dias vs. um mínimo configurável.
- **Veranico**: dias secos consecutivos, com projeção pela previsão. O alerta só
  soa em fases sensíveis do calendário agrícola; na entressafra é tratado como normal.
- **Calor**: dias da previsão acima do limite configurável (padrão 35°C).
- **Previsão de 7 dias** em cards responsivos (funcionam bem no celular).
- **Gráficos**: chuva acumulada (com faixa mín–máx histórica), chuva por ano,
  temperatura e chuva diária.
- **Comparar polos**: os 5 polos lado a lado (carregado sob demanda).
- **Link compartilhável** (`?cidade=Dourados+(MS)`) e **download CSV**.

## Notas técnicas

- Parâmetros agronômicos (mínimo de chuva para plantio, limite de calor, dias de
  veranico) são **heurísticas ajustáveis** na barra lateral — não substituem
  recomendação agronômica local, que varia por cultivar, latitude e safra.
- Datas calculadas no fuso de cada cidade (o servidor da nuvem roda em UTC).
- Histórico baixado com concorrência baixa e cache de 24h; a API pública limita
  requisições simultâneas, então o app falha de forma graciosa e tenta de novo.
- Fases do calendário agrícola são aproximações indicativas por mês.
