"""
Dashboard de Clima x Agro — Polos produtores de soja/milho do Centro-Oeste
Monitora chuva acumulada, veranico (dry spell) e temperatura, comparando
com o mesmo período do ano passado e com a média histórica de 5 anos,
para apoiar leitura de janela de plantio e condições de lavoura.

Fonte de dados: Open-Meteo (https://open-meteo.com) — API pública e gratuita,
sem necessidade de chave/token.
"""

import re
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuração geral
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Clima x Agro | Centro-Oeste",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

CITIES = {
    "Sorriso (MT)": {"lat": -12.5455, "lon": -55.7108, "tz": "America/Cuiaba"},
    "Rondonópolis (MT)": {"lat": -16.4673, "lon": -54.6372, "tz": "America/Cuiaba"},
    "Primavera do Leste (MT)": {"lat": -15.5567, "lon": -54.2967, "tz": "America/Cuiaba"},
    "Rio Verde (GO)": {"lat": -17.7943, "lon": -50.9269, "tz": "America/Sao_Paulo"},
    "Dourados (MS)": {"lat": -22.2211, "lon": -54.8056, "tz": "America/Campo_Grande"},
}

PAST_DAYS = 30       # janela retroativa (chuva acumulada / veranico observado)
FORECAST_DAYS = 7    # janela de previsão (chuva/temperatura futuras)
HIST_YEARS = 5        # anos usados na "média histórica"
DRY_MM = 1.0           # limiar de "dia seco" (mm)
VERANICO_ALERT_DAYS = 10  # a partir de quantos dias secos seguidos alertar (em época de cultivo)

WEATHER_CODES = {
    0: ("☀️", "Céu limpo"),
    1: ("🌤️", "Poucas nuvens"),
    2: ("⛅", "Parcialmente nublado"),
    3: ("☁️", "Nublado"),
    45: ("🌫️", "Nevoeiro"),
    48: ("🌫️", "Nevoeiro c/ geada"),
    51: ("🌦️", "Garoa fraca"),
    53: ("🌦️", "Garoa"),
    55: ("🌦️", "Garoa forte"),
    56: ("🌦️", "Garoa congelante"),
    57: ("🌦️", "Garoa congelante forte"),
    61: ("🌧️", "Chuva fraca"),
    63: ("🌧️", "Chuva"),
    65: ("🌧️", "Chuva forte"),
    66: ("🌧️", "Chuva congelante"),
    67: ("🌧️", "Chuva congelante forte"),
    71: ("🌨️", "Neve fraca"),
    73: ("🌨️", "Neve"),
    75: ("🌨️", "Neve forte"),
    77: ("🌨️", "Grãos de neve"),
    80: ("🌧️", "Pancadas de chuva fracas"),
    81: ("🌧️", "Pancadas de chuva"),
    82: ("⛈️", "Pancadas de chuva fortes"),
    85: ("🌨️", "Pancadas de neve fracas"),
    86: ("🌨️", "Pancadas de neve fortes"),
    95: ("⛈️", "Trovoada"),
    96: ("⛈️", "Trovoada c/ granizo"),
    99: ("⛈️", "Trovoada c/ granizo forte"),
}

WEEKDAY_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode"
DAILY_VARS_ARCHIVE = "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"


# ---------------------------------------------------------------------------
# Funções de acesso à API (com cache)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_current(lat: float, lon: float, tz: str, past_days: int, forecast_days: int) -> pd.DataFrame:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": DAILY_VARS,
        "timezone": tz,
        "past_days": past_days,
        "forecast_days": forecast_days,
    }
    r = requests.get(FORECAST_URL, params=params, timeout=15)
    r.raise_for_status()
    daily = r.json()["daily"]
    df = pd.DataFrame(daily)
    df["time"] = pd.to_datetime(df["time"])
    return df


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def fetch_archive(lat: float, lon: float, tz: str, start: date, end: date) -> pd.DataFrame:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_VARS_ARCHIVE,
        "timezone": tz,
    }
    r = requests.get(ARCHIVE_URL, params=params, timeout=15)
    r.raise_for_status()
    daily = r.json().get("daily", {})
    if not daily or not daily.get("time"):
        return pd.DataFrame(columns=["time"] + DAILY_VARS_ARCHIVE.split(","))
    df = pd.DataFrame(daily)
    df["time"] = pd.to_datetime(df["time"])
    return df


def safe_replace_year(d: date, years_back: int) -> date:
    """Subtrai N anos de uma data, tratando 29/fev."""
    try:
        return d.replace(year=d.year - years_back)
    except ValueError:
        return d.replace(year=d.year - years_back, day=28)


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def build_comparison(lat: float, lon: float, tz: str, today: date) -> dict:
    window_start = today - timedelta(days=PAST_DAYS)
    window_end = today + timedelta(days=FORECAST_DAYS - 1)
    window_len = PAST_DAYS + FORECAST_DAYS
    dates = [window_start + timedelta(days=i) for i in range(window_len)]

    current_df = fetch_current(lat, lon, tz, PAST_DAYS, FORECAST_DAYS)

    # Ano passado — mesma janela de calendário, 1 ano atrás (dados reais)
    ly_start = safe_replace_year(window_start, 1)
    ly_end = safe_replace_year(window_end, 1)
    last_year_df = fetch_archive(lat, lon, tz, ly_start, ly_end)

    # Média histórica — mesma janela de calendário, últimos N anos
    per_offset_max = [[] for _ in range(window_len)]
    per_offset_min = [[] for _ in range(window_len)]
    per_offset_precip = [[] for _ in range(window_len)]
    for y in range(1, HIST_YEARS + 1):
        start_y = safe_replace_year(window_start, y)
        end_y = safe_replace_year(window_end, y)
        yr_df = fetch_archive(lat, lon, tz, start_y, end_y)
        for i in range(min(window_len, len(yr_df))):
            row = yr_df.iloc[i]
            if pd.notna(row.get("temperature_2m_max")):
                per_offset_max[i].append(row["temperature_2m_max"])
            if pd.notna(row.get("temperature_2m_min")):
                per_offset_min[i].append(row["temperature_2m_min"])
            if pd.notna(row.get("precipitation_sum")):
                per_offset_precip[i].append(row["precipitation_sum"])

    hist_avg = pd.DataFrame(
        {
            "offset": list(range(-PAST_DAYS, FORECAST_DAYS)),
            "temperature_2m_max": [sum(v) / len(v) if v else None for v in per_offset_max],
            "temperature_2m_min": [sum(v) / len(v) if v else None for v in per_offset_min],
            "precipitation_sum": [sum(v) / len(v) if v else None for v in per_offset_precip],
        }
    )

    return {
        "dates": dates,
        "window_start": window_start,
        "window_end": window_end,
        "current": current_df,
        "last_year": last_year_df,
        "hist_avg": hist_avg,
    }


# ---------------------------------------------------------------------------
# Funções auxiliares de apresentação
# ---------------------------------------------------------------------------


def weekday_label(d: date) -> str:
    return WEEKDAY_PT[d.weekday()]


def get_agro_phase(d: date) -> tuple[str, bool]:
    """Retorna (rótulo da fase agrícola típica, se é época sensível a veranico)."""
    m = d.month
    if m in (9, 10, 11):
        return "Plantio da soja", True
    if m == 12 or m == 1:
        return "Desenvolvimento da soja / plantio da safrinha de milho", True
    if m in (2, 3):
        return "Enchimento de grãos / colheita da soja", True
    if m in (4, 5, 6):
        return "Colheita e desenvolvimento da safrinha de milho", True
    return "Entressafra (estação seca)", False


def trailing_dry_streak(values, threshold=DRY_MM) -> int:
    """Conta dias consecutivos abaixo do limiar, terminando no último elemento da lista."""
    count = 0
    for v in reversed(list(values)):
        if v is None or pd.isna(v):
            break
        if v < threshold:
            count += 1
        else:
            break
    return count


def fmt_delta_mm(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.0f} mm"


def fmt_delta_c(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}°C"


def md_bold_to_html(text: str) -> str:
    """Converte **negrito** markdown em <b> — necessário pois o card de
    insight é montado como HTML bruto (unsafe_allow_html), que não passa
    pelo parser de markdown do Streamlit."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


# ---------------------------------------------------------------------------
# CSS — estilo dashboard financeiro (dark mode contrastado)
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp { background-color: #0e1117; }
    .metric-card {
        background: #161b26;
        border: 1px solid #262d3d;
        border-radius: 10px;
        padding: 18px 20px;
        margin-bottom: 10px;
    }
    .metric-label {
        font-size: 0.80rem;
        color: #8b93a7;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 4px;
    }
    .metric-value {
        font-size: 1.9rem;
        font-weight: 700;
        color: #f2f4f8;
    }
    .metric-sub {
        font-size: 0.85rem;
        color: #8b93a7;
        margin-top: 2px;
    }
    .insight-card {
        background: linear-gradient(135deg, #161b26 0%, #1c2a1f 100%);
        border-left: 4px solid #4fd18c;
        border-radius: 10px;
        padding: 20px 22px;
        margin: 14px 0;
    }
    .insight-card.alert {
        border-left-color: #ff6b6b;
    }
    .insight-card.positive {
        border-left-color: #4fd18c;
    }
    .day-card {
        background: #161b26;
        border: 1px solid #262d3d;
        border-radius: 10px;
        padding: 14px 10px;
        text-align: center;
    }
    .day-card .day-name { font-weight: 600; color: #f2f4f8; font-size: 0.95rem; }
    .day-card .day-icon { font-size: 1.8rem; margin: 6px 0; }
    .day-card .day-temp { color: #f2f4f8; font-size: 1.0rem; }
    .day-card .day-temp .max { font-weight: 700; }
    .day-card .day-temp .min { color: #8b93a7; }
    .day-card .day-rain { font-size: 0.78rem; color: #6fb1ff; margin-top: 4px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🌱 Clima x Agro")
st.sidebar.caption("Monitoramento climático para leitura de safra — soja e milho")

city_name = st.sidebar.selectbox("Selecione o polo produtor", list(CITIES.keys()))
city = CITIES[city_name]

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"""
    **Sobre o app**

    Acompanha chuva acumulada nos últimos {PAST_DAYS} dias, previsão para os
    próximos {FORECAST_DAYS} dias e veranico (sequência de dias secos),
    comparando com o mesmo período do ano passado e com a média histórica
    (últimos {HIST_YEARS} anos), por polo produtor do Centro-Oeste.

    Dados: [Open-Meteo](https://open-meteo.com) (gratuito, sem chave).
    """
)

if st.sidebar.button("🔄 Atualizar dados"):
    st.cache_data.clear()

# ---------------------------------------------------------------------------
# Carregamento de dados
# ---------------------------------------------------------------------------

today = date.today()

with st.spinner(f"Buscando dados climáticos para {city_name}..."):
    try:
        data = build_comparison(city["lat"], city["lon"], city["tz"], today)
    except requests.exceptions.RequestException as e:
        st.error(f"Erro ao consultar a API Open-Meteo: {e}")
        st.stop()

current_df = data["current"]
last_year_df = data["last_year"]
hist_avg_df = data["hist_avg"]
dates = data["dates"]

window_len = len(dates)
n_ly = min(window_len, len(last_year_df))

precip_now = current_df["precipitation_sum"].tolist()
precip_ly = last_year_df["precipitation_sum"].tolist() if n_ly else []
precip_hist = hist_avg_df["precipitation_sum"].tolist()

phase_label, is_sensitive_phase = get_agro_phase(today)

# ---------------------------------------------------------------------------
# Cabeçalho
# ---------------------------------------------------------------------------

st.title(f"📍 {city_name} — Painel de Clima para Agro")
st.caption(
    f"Janela de {PAST_DAYS} dias observados + {FORECAST_DAYS} dias de previsão, "
    f"comparada aos mesmos dias de {today.year - 1} e à média dos últimos {HIST_YEARS} anos. "
    f"Fase típica do calendário agrícola: **{phase_label}**."
)

# ---------------------------------------------------------------------------
# Cards diários — previsão dos próximos dias
# ---------------------------------------------------------------------------

st.subheader(f"Previsão dos próximos {FORECAST_DAYS} dias")
fwd_df = current_df.iloc[PAST_DAYS:PAST_DAYS + FORECAST_DAYS].reset_index(drop=True)
fwd_dates = dates[PAST_DAYS:PAST_DAYS + FORECAST_DAYS]
n_fwd = len(fwd_df)

day_cols = st.columns(n_fwd)
for i in range(n_fwd):
    row = fwd_df.iloc[i]
    d = fwd_dates[i]
    icon, desc = WEATHER_CODES.get(int(row["weathercode"]), ("❓", "N/D"))
    with day_cols[i]:
        st.markdown(
            f"""
            <div class="day-card">
                <div class="day-name">{weekday_label(d)} {d.strftime('%d/%m')}</div>
                <div class="day-icon">{icon}</div>
                <div class="day-temp">
                    <span class="max">{row['temperature_2m_max']:.0f}°</span>
                    &nbsp;/&nbsp;
                    <span class="min">{row['temperature_2m_min']:.0f}°</span>
                </div>
                <div class="day-rain">💧 {row.get('precipitation_probability_max', 0):.0f}% chuva</div>
                <div class="day-rain">{desc}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Cálculo dos indicadores agro
# ---------------------------------------------------------------------------

past_now = precip_now[:PAST_DAYS]
fwd_now = precip_now[PAST_DAYS:PAST_DAYS + FORECAST_DAYS]
past_ly = precip_ly[:PAST_DAYS] if n_ly >= PAST_DAYS else precip_ly
fwd_ly = precip_ly[PAST_DAYS:PAST_DAYS + FORECAST_DAYS] if n_ly > PAST_DAYS else []
past_hist = precip_hist[:PAST_DAYS]
fwd_hist = precip_hist[PAST_DAYS:PAST_DAYS + FORECAST_DAYS]

accum_past_now = sum(v for v in past_now if pd.notna(v))
accum_past_ly = sum(v for v in past_ly if pd.notna(v)) if past_ly else None
accum_past_hist = sum(v for v in past_hist if pd.notna(v)) if any(pd.notna(v) for v in past_hist) else None

accum_fwd_now = sum(v for v in fwd_now if pd.notna(v))
accum_fwd_ly = sum(v for v in fwd_ly if pd.notna(v)) if fwd_ly else None
accum_fwd_hist = sum(v for v in fwd_hist if pd.notna(v)) if any(pd.notna(v) for v in fwd_hist) else None

dev_past_ly = (accum_past_now - accum_past_ly) if accum_past_ly is not None else None
dev_past_hist = (accum_past_now - accum_past_hist) if accum_past_hist is not None else None

# Veranico: sequência de dias secos terminando ontem (observado)
current_dry_streak = trailing_dry_streak(past_now)
ly_dry_streak = trailing_dry_streak(past_ly) if past_ly else None

# Projeção: se a previsão se confirmar, quantos dias secos seguidos até quando
projected_streak = current_dry_streak
for v in fwd_now:
    if pd.notna(v) and v < DRY_MM:
        projected_streak += 1
    else:
        break

avg_max_fwd = pd.Series(fwd_now_max := current_df["temperature_2m_max"].iloc[PAST_DAYS:PAST_DAYS + FORECAST_DAYS]).mean()
avg_max_fwd_ly = (
    last_year_df["temperature_2m_max"].iloc[PAST_DAYS:PAST_DAYS + FORECAST_DAYS].mean()
    if n_ly > PAST_DAYS
    else None
)
avg_max_fwd_hist = pd.Series(fwd_hist_max := hist_avg_df["temperature_2m_max"].iloc[PAST_DAYS:PAST_DAYS + FORECAST_DAYS]).mean()

dev_temp_ly = (avg_max_fwd - avg_max_fwd_ly) if avg_max_fwd_ly is not None and pd.notna(avg_max_fwd_ly) else None
dev_temp_hist = (avg_max_fwd - avg_max_fwd_hist) if pd.notna(avg_max_fwd_hist) else None

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

st.subheader("Painel de Insights Agro")

kpi_cols = st.columns(4)

with kpi_cols[0]:
    sub = f"vs. {today.year - 1}: {accum_past_ly:.0f} mm" if accum_past_ly is not None else "vs. ano passado: N/D"
    color = "#4fd18c" if (dev_past_ly or 0) >= 0 else "#ff9f4f"
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Chuva acumulada ({PAST_DAYS}d)</div>
            <div class="metric-value">{accum_past_now:.0f} mm</div>
            <div class="metric-sub" style="color:{color}">{fmt_delta_mm(dev_past_ly) if dev_past_ly is not None else ''}</div>
            <div class="metric-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[1]:
    sub = f"vs. {today.year - 1}: {accum_fwd_ly:.0f} mm" if accum_fwd_ly is not None else "vs. ano passado: N/D"
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Chuva prevista ({FORECAST_DAYS}d)</div>
            <div class="metric-value">{accum_fwd_now:.0f} mm</div>
            <div class="metric-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[2]:
    color = "#ff6b6b" if (is_sensitive_phase and current_dry_streak >= VERANICO_ALERT_DAYS) else "#f2f4f8"
    sub = f"ano passado (mesma data): {ly_dry_streak} dias" if ly_dry_streak is not None else "ano passado: N/D"
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Veranico atual</div>
            <div class="metric-value" style="color:{color}">{current_dry_streak} dias</div>
            <div class="metric-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[3]:
    if dev_temp_ly is not None:
        color = "#ff6b6b" if dev_temp_ly > 0 else "#4f8cff"
        sub = fmt_delta_c(dev_temp_ly) + " vs. ano passado"
    else:
        color = "#f2f4f8"
        sub = "vs. ano passado: N/D"
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">Temp. média máx. prevista</div>
            <div class="metric-value" style="color:{color}">{avg_max_fwd:.1f}°C</div>
            <div class="metric-sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Card de insight textual (leitura agro)
# ---------------------------------------------------------------------------

insight_lines = []
card_class = "insight-card"

if accum_past_ly is not None:
    pct = (accum_past_now / accum_past_ly - 1) * 100 if accum_past_ly > 0 else None
    direction = "acima" if accum_past_now >= accum_past_ly else "abaixo"
    pct_txt = f" ({abs(pct):.0f}% {direction})" if pct is not None else ""
    insight_lines.append(
        f"🌧️ Nos últimos **{PAST_DAYS} dias**, **{city_name}** acumulou **{accum_past_now:.0f} mm** de "
        f"chuva, contra **{accum_past_ly:.0f} mm** no mesmo período de {today.year - 1}{pct_txt}."
    )

if accum_past_hist is not None:
    direction_h = "acima" if dev_past_hist >= 0 else "abaixo"
    insight_lines.append(
        f"📊 Isso está **{abs(dev_past_hist):.0f} mm {direction_h}** da média histórica dos últimos "
        f"{HIST_YEARS} anos ({accum_past_hist:.0f} mm) para essas datas."
    )

if accum_fwd_ly is not None:
    insight_lines.append(
        f"🔮 Para os próximos **{FORECAST_DAYS} dias**, a previsão acumula **{accum_fwd_now:.0f} mm**, "
        f"contra **{accum_fwd_ly:.0f} mm** observados no mesmo período do ano passado."
    )

# Veranico — só soa alerta em época sensível (plantio/desenvolvimento)
if is_sensitive_phase and current_dry_streak >= VERANICO_ALERT_DAYS:
    insight_lines.append(
        f"⚠️ **Alerta de veranico:** **{current_dry_streak} dias consecutivos** sem chuva "
        f"relevante (< {DRY_MM:.0f} mm/dia) durante a fase de **{phase_label.lower()}**"
        + (f" — no ano passado, na mesma data, eram {ly_dry_streak} dias." if ly_dry_streak is not None else ".")
        + " Risco para germinação/desenvolvimento da lavoura."
    )
    card_class += " alert"
elif current_dry_streak >= VERANICO_ALERT_DAYS:
    insight_lines.append(
        f"☀️ **{current_dry_streak} dias consecutivos** sem chuva relevante — normal para a "
        f"**{phase_label.lower()}**, sem sinal de alerta neste momento."
    )
elif is_sensitive_phase and accum_past_ly is not None and accum_past_now < accum_past_ly * 0.7:
    insight_lines.append(
        f"⚠️ **Atenção:** chuva acumulada bem abaixo do ano passado durante a **{phase_label.lower()}** — "
        f"pode indicar atraso na janela ideal de plantio/desenvolvimento."
    )
    card_class += " alert"
elif is_sensitive_phase and accum_past_ly is not None and accum_past_now > accum_past_ly * 1.3:
    insight_lines.append(
        f"✅ Chuva acumulada bem acima do ano passado durante a **{phase_label.lower()}** — "
        f"condições de umidade favoráveis para a lavoura."
    )
    card_class += " positive"

if projected_streak > current_dry_streak:
    fwd_end_date = fwd_dates[min(projected_streak - current_dry_streak, n_fwd) - 1] if n_fwd else None
    if fwd_end_date is not None:
        insight_lines.append(
            f"📅 Se a previsão se confirmar, o veranico deve se estender a **{projected_streak} dias** "
            f"até **{weekday_label(fwd_end_date)} {fwd_end_date.strftime('%d/%m')}**."
        )

if not insight_lines:
    insight_lines.append("Sem desvios relevantes identificados para o período.")

insight_html = "<br><br>".join(md_bold_to_html(line) for line in insight_lines)
st.markdown(
    f'<div class="{card_class}">{insight_html}</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Gráficos comparativos
# ---------------------------------------------------------------------------

x_labels = [f"{d.strftime('%d/%m')}" for d in dates]
today_label = today.strftime("%d/%m")

tab1, tab2, tab3 = st.tabs(["💧 Chuva acumulada", "🌡️ Temperatura", "🌧️ Chuva diária"])

with tab1:
    cum_now = pd.Series(precip_now).fillna(0).cumsum()
    cum_ly = pd.Series(precip_ly).fillna(0).cumsum() if precip_ly else None
    cum_hist = pd.Series(precip_hist).fillna(0).cumsum()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=cum_now,
            name=f"{today.year} (observado + previsão)",
            mode="lines",
            line=dict(color="#4fd18c", width=3),
        )
    )
    if cum_ly is not None:
        fig.add_trace(
            go.Scatter(
                x=x_labels[: len(cum_ly)],
                y=cum_ly,
                name=f"{today.year - 1} (ano passado)",
                mode="lines",
                line=dict(color="#8b93a7", width=2, dash="dot"),
            )
        )
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=cum_hist,
            name=f"Média {HIST_YEARS} anos",
            mode="lines",
            line=dict(color="#4f8cff", width=2, dash="dash"),
        )
    )
    fig.add_vline(x=today_label, line_width=1, line_dash="dot", line_color="#f2f4f8")
    fig.add_annotation(x=today_label, y=1, yref="paper", text="hoje", showarrow=False, yshift=10, font=dict(color="#f2f4f8", size=11))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=40, l=10, r=10, b=10),
        yaxis_title="Chuva acumulada (mm)",
        xaxis=dict(tickangle=-45, dtick=2),
        height=460,
    )
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=x_labels,
            y=current_df["temperature_2m_max"],
            name=f"Máx. {today.year}",
            mode="lines",
            line=dict(color="#ff6b6b", width=2.5),
        )
    )
    fig2.add_trace(
        go.Scatter(
            x=x_labels,
            y=current_df["temperature_2m_min"],
            name=f"Mín. {today.year}",
            mode="lines",
            line=dict(color="#4f8cff", width=2.5),
        )
    )
    if n_ly:
        fig2.add_trace(
            go.Scatter(
                x=x_labels[:n_ly],
                y=last_year_df["temperature_2m_max"].iloc[:n_ly],
                name=f"Máx. {today.year - 1}",
                mode="lines",
                line=dict(color="#ff6b6b", width=1.5, dash="dot"),
            )
        )
    fig2.add_vline(x=today_label, line_width=1, line_dash="dot", line_color="#f2f4f8")
    fig2.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=40, l=10, r=10, b=10),
        yaxis_title="Temperatura (°C)",
        xaxis=dict(tickangle=-45, dtick=2),
        height=460,
    )
    st.plotly_chart(fig2, use_container_width=True)

with tab3:
    fig3 = go.Figure()
    fig3.add_trace(
        go.Bar(
            x=x_labels,
            y=precip_now,
            name=f"{today.year}",
            marker_color="#4fd18c",
        )
    )
    if precip_ly:
        fig3.add_trace(
            go.Bar(
                x=x_labels[: len(precip_ly)],
                y=precip_ly,
                name=f"{today.year - 1}",
                marker_color="#8b93a7",
            )
        )
    fig3.add_vline(x=today_label, line_width=1, line_dash="dot", line_color="#f2f4f8")
    fig3.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=40, l=10, r=10, b=10),
        yaxis_title="Precipitação (mm)",
        xaxis=dict(tickangle=-45, dtick=2),
        height=420,
    )
    st.plotly_chart(fig3, use_container_width=True)

# ---------------------------------------------------------------------------
# Tabela de dados brutos
# ---------------------------------------------------------------------------

with st.expander("📄 Ver dados brutos"):
    table = pd.DataFrame(
        {
            "Data": [d.strftime("%d/%m/%Y") for d in dates],
            "Máx atual (°C)": current_df["temperature_2m_max"].values,
            "Mín atual (°C)": current_df["temperature_2m_min"].values,
            "Chuva atual (mm)": current_df["precipitation_sum"].values,
            "Chuva ano passado (mm)": (
                list(last_year_df["precipitation_sum"].values) + [None] * (window_len - n_ly)
                if n_ly
                else [None] * window_len
            ),
            f"Chuva média {HIST_YEARS}a (mm)": hist_avg_df["precipitation_sum"].values,
        }
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

st.caption(
    "Dados fornecidos por Open-Meteo.com. Fases do calendário agrícola são aproximações "
    "indicativas para o Centro-Oeste e não substituem recomendação agronômica local. "
    "Este painel tem finalidade analítica/informativa."
)
