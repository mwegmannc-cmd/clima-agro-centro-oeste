"""
Dashboard de Clima x Agro — Polos produtores de soja/milho do Centro-Oeste

Monitora chuva acumulada, janela de plantio, veranico (dry spell), calor e
temperatura, comparando com o mesmo período do ano passado e com a média
histórica (10 anos), para apoiar a leitura de condições de lavoura.

Fonte de dados: Open-Meteo (https://open-meteo.com) — API pública e gratuita,
sem necessidade de chave/token.
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
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
    initial_sidebar_state="auto",
)

CITIES = {
    "Sorriso (MT)": {"lat": -12.5455, "lon": -55.7108, "tz": "America/Cuiaba"},
    "Rondonópolis (MT)": {"lat": -16.4673, "lon": -54.6372, "tz": "America/Cuiaba"},
    "Primavera do Leste (MT)": {"lat": -15.5567, "lon": -54.2967, "tz": "America/Cuiaba"},
    "Rio Verde (GO)": {"lat": -17.7943, "lon": -50.9269, "tz": "America/Sao_Paulo"},
    "Dourados (MS)": {"lat": -22.2211, "lon": -54.8056, "tz": "America/Campo_Grande"},
}

FORECAST_DAYS = 7      # janela de previsão
HIST_YEARS = 10        # anos usados na média/ranking históricos
DRY_MM = 1.0           # limiar de "dia seco" (mm)
BRT = ZoneInfo("America/Sao_Paulo")

WEATHER_CODES = {
    0: ("☀️", "Céu limpo"), 1: ("🌤️", "Poucas nuvens"), 2: ("⛅", "Parc. nublado"),
    3: ("☁️", "Nublado"), 45: ("🌫️", "Nevoeiro"), 48: ("🌫️", "Nevoeiro"),
    51: ("🌦️", "Garoa fraca"), 53: ("🌦️", "Garoa"), 55: ("🌦️", "Garoa forte"),
    56: ("🌦️", "Garoa gelada"), 57: ("🌦️", "Garoa gelada"),
    61: ("🌧️", "Chuva fraca"), 63: ("🌧️", "Chuva"), 65: ("🌧️", "Chuva forte"),
    66: ("🌧️", "Chuva gelada"), 67: ("🌧️", "Chuva gelada"),
    71: ("🌨️", "Neve"), 73: ("🌨️", "Neve"), 75: ("🌨️", "Neve"), 77: ("🌨️", "Neve"),
    80: ("🌧️", "Pancadas fracas"), 81: ("🌧️", "Pancadas"), 82: ("⛈️", "Pancadas fortes"),
    85: ("🌨️", "Neve"), 86: ("🌨️", "Neve"),
    95: ("⛈️", "Trovoada"), 96: ("⛈️", "Trovoada c/ granizo"), 99: ("⛈️", "Trovoada c/ granizo"),
}

WEEKDAY_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode"
DAILY_VARS_ARCHIVE = "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode"

TONE = {"good": "#4fd18c", "bad": "#ff6b6b", "warn": "#ffb454", "cold": "#4f8cff", "neutral": "#f2f4f8"}


# ---------------------------------------------------------------------------
# Acesso à API
# ---------------------------------------------------------------------------


def _get_json(url: str, params: dict, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:  # limite de requisições: espera e tenta de novo
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(0.8 * (attempt + 1))
    raise last or RuntimeError("A API Open-Meteo não respondeu (limite de requisições).")


def _to_df(payload: dict, cols: list[str]) -> pd.DataFrame:
    daily = payload.get("daily") or {}
    if not daily.get("time"):
        return pd.DataFrame(columns=["time"] + cols)
    df = pd.DataFrame(daily)
    df["time"] = pd.to_datetime(df["time"]).dt.date
    return df


def safe_replace_year(d: date, years_back: int) -> date:
    """Subtrai N anos de uma data, tratando 29/fev."""
    try:
        return d.replace(year=d.year - years_back)
    except ValueError:
        return d.replace(year=d.year - years_back, day=28)


def align(df: pd.DataFrame | None, dates: list[date], years_back: int) -> pd.DataFrame | None:
    """Reindexa por data (deslocada N anos) — robusto a anos bissextos."""
    if df is None or df.empty:
        return None
    keys = [safe_replace_year(d, years_back) for d in dates]
    return df.set_index("time").reindex(keys).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_current(lat: float, lon: float, tz: str, past_days: int, forecast_days: int):
    payload = _get_json(
        FORECAST_URL,
        {
            "latitude": lat, "longitude": lon, "daily": DAILY_VARS, "timezone": tz,
            "past_days": past_days, "forecast_days": forecast_days,
        },
    )
    return _to_df(payload, DAILY_VARS.split(",")), datetime.now(BRT)


@st.cache_data(ttl=86400, show_spinner=False)
def load_history(lat: float, lon: float, tz: str, start: date, end: date, n_years: int) -> dict:
    """Baixa, em paralelo, a mesma janela de calendário dos últimos N anos."""

    def one(y: int):
        try:
            payload = _get_json(
                ARCHIVE_URL,
                {
                    "latitude": lat, "longitude": lon, "daily": DAILY_VARS_ARCHIVE, "timezone": tz,
                    "start_date": safe_replace_year(start, y).isoformat(),
                    "end_date": safe_replace_year(end, y).isoformat(),
                },
            )
            return y, _to_df(payload, DAILY_VARS_ARCHIVE.split(","))
        except Exception:
            return y, None

    # concorrência baixa: a API pública limita requisições simultâneas por IP
    with ThreadPoolExecutor(max_workers=3) as ex:
        result = {y: df for y, df in ex.map(one, range(1, n_years + 1)) if df is not None}
    if len(result) < max(1, n_years // 2):
        # exceção não é cacheada: evita guardar por 24h um histórico incompleto
        raise RuntimeError("A API Open-Meteo está limitando requisições no momento.")
    return result


def build_data(city: dict, today: date, past_days: int) -> dict:
    dates = [today + timedelta(days=o) for o in range(-past_days, FORECAST_DAYS)]
    cur_raw, fetched_at = load_current(city["lat"], city["lon"], city["tz"], past_days, FORECAST_DAYS)
    hist_raw = load_history(city["lat"], city["lon"], city["tz"], dates[0], dates[-1], HIST_YEARS)
    years = {y: align(df, dates, y) for y, df in hist_raw.items()}
    return {
        "dates": dates,
        "past_days": past_days,
        "current": align(cur_raw, dates, 0),
        "years": {y: d for y, d in years.items() if d is not None},
        "fetched_at": fetched_at,
    }


# ---------------------------------------------------------------------------
# Indicadores
# ---------------------------------------------------------------------------


def get_agro_phase(d: date) -> tuple[str, bool, bool]:
    """(fase típica do calendário, sensível a veranico?, época de plantio da soja?)"""
    m = d.month
    if m in (9, 10, 11):
        return "Plantio da soja", True, True
    if m == 12:
        return "Desenvolvimento da soja", True, False
    if m == 1:
        return "Desenvolvimento da soja / início do plantio da safrinha", True, False
    if m in (2, 3):
        return "Colheita da soja / plantio da safrinha de milho", True, False
    if m in (4, 5):
        return "Desenvolvimento da safrinha de milho", True, False
    if m == 6:
        return "Colheita da safrinha de milho", False, False
    return "Entressafra (estação seca)", False, False


def trailing_dry_streak(values) -> int:
    n = 0
    for v in reversed(list(values)):
        if v is None or np.isnan(v) or v >= DRY_MM:
            break
        n += 1
    return n


def pct(a, b):
    """Variação percentual de a sobre b (None se a base for ~0)."""
    if a is None or b is None or b < 1:
        return None
    return (a / b - 1) * 100


def compute_metrics(data: dict, params: dict) -> dict:
    P, F = data["past_days"], FORECAST_DAYS
    cur, yrs = data["current"], data["years"]
    ly = yrs.get(1)

    def A(df, col):
        return None if df is None else df[col].to_numpy(dtype=float)

    def ssum(a, sl):
        if a is None:
            return None
        s = a[sl]
        return None if np.all(np.isnan(s)) else float(np.nansum(s))

    def smean(a, sl):
        if a is None:
            return None
        s = a[sl]
        return None if np.all(np.isnan(s)) else float(np.nanmean(s))

    past, fwd, last15 = slice(0, P), slice(P, P + F), slice(max(P - 15, 0), P)

    rain_now, rain_ly = A(cur, "precipitation_sum"), A(ly, "precipitation_sum")
    tmax_now, tmin_now = A(cur, "temperature_2m_max"), A(cur, "temperature_2m_min")
    tmax_ly = A(ly, "temperature_2m_max")

    hist_rain = pd.DataFrame({y: A(d, "precipitation_sum") for y, d in yrs.items()}) if yrs else None
    hist_tmax = pd.DataFrame({y: A(d, "temperature_2m_max") for y, d in yrs.items()}) if yrs else None
    hist_rain_avg = hist_rain.mean(axis=1).to_numpy() if hist_rain is not None else None
    hist_tmax_avg = hist_tmax.mean(axis=1).to_numpy() if hist_tmax is not None else None

    year_sums = hist_rain.iloc[past].sum(min_count=1).dropna() if hist_rain is not None else pd.Series(dtype=float)
    year_sums_15 = hist_rain.iloc[last15].sum(min_count=1).dropna() if hist_rain is not None else pd.Series(dtype=float)

    m = {
        "rain_past": ssum(rain_now, past),
        "rain_past_ly": ssum(rain_ly, past),
        "rain_past_hist": float(year_sums.mean()) if len(year_sums) else None,
        "rain_fwd": ssum(rain_now, fwd),
        "rain_fwd_ly": ssum(rain_ly, fwd),
        "rain_fwd_hist": float(hist_rain.iloc[fwd].sum(min_count=1).mean()) if hist_rain is not None else None,
        "rain15": ssum(rain_now, last15),
        "rain15_hist": float(year_sums_15.mean()) if len(year_sums_15) else None,
        "tmax_fwd": smean(tmax_now, fwd),
        "tmax_fwd_ly": smean(tmax_ly, fwd),
        "tmax_fwd_hist": smean(hist_tmax_avg, fwd),
        "heat_days": int(np.sum(tmax_now[fwd] >= params["heat_c"])) if tmax_now is not None else 0,
        "dry_streak": trailing_dry_streak(rain_now[past]),
        "dry_streak_ly": trailing_dry_streak(rain_ly[past]) if rain_ly is not None else None,
        "year_sums": year_sums,
        "arrays": {
            "rain_now": rain_now, "rain_ly": rain_ly, "hist_rain": hist_rain, "hist_rain_avg": hist_rain_avg,
            "tmax_now": tmax_now, "tmin_now": tmin_now, "tmax_ly": tmax_ly, "hist_tmax_avg": hist_tmax_avg,
        },
    }

    # projeção do veranico se a previsão se confirmar
    proj = m["dry_streak"]
    for v in rain_now[fwd]:
        if not np.isnan(v) and v < DRY_MM:
            proj += 1
        else:
            break
    m["dry_projected"] = proj

    # ranking de chuva da janela (1 = mais chuvoso da série)
    if m["rain_past"] is not None and len(year_sums):
        m["rank"] = 1 + int((year_sums > m["rain_past"]).sum())
        m["rank_total"] = len(year_sums) + 1
    else:
        m["rank"] = m["rank_total"] = None

    # janela de plantio: chuva dos últimos 15 dias vs mínimo configurado
    if m["rain15"] is None:
        m["plant_status"] = None
    elif m["rain15"] >= params["planting_mm"]:
        m["plant_status"] = "Favorável"
    elif m["rain15"] >= 0.5 * params["planting_mm"]:
        m["plant_status"] = "Parcial"
    else:
        m["plant_status"] = "Insuficiente"
    return m


# ---------------------------------------------------------------------------
# Helpers de apresentação
# ---------------------------------------------------------------------------


def md_bold_to_html(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def fmt_mm(v, signed=False):
    if v is None:
        return "N/D"
    return f"{v:+.0f} mm" if signed else f"{v:.0f} mm"


def delta_txt(a, b, unit="mm"):
    """'+12 mm (+8%)' de a sobre b."""
    if a is None or b is None:
        return None
    p = pct(a, b)
    return f"{a - b:+.0f} {unit}" + (f" ({p:+.0f}%)" if p is not None else "")


def kpi_card(label, value, delta=None, sub=None, tone="neutral", delta_tone="neutral"):
    parts = [f'<div class="kpi"><div class="kpi-label">{label}</div>']
    parts.append(f'<div class="kpi-value" style="color:{TONE[tone]}">{value}</div>')
    if delta:
        parts.append(f'<div class="kpi-delta" style="color:{TONE[delta_tone]}">{delta}</div>')
    if sub:
        parts.append(f'<div class="kpi-sub">{sub}</div>')
    parts.append("</div>")
    return "".join(parts)


def style_fig(fig, labels, height=440, ytitle=""):
    step = max(1, len(labels) // 12)
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified", dragmode=False, height=height,
        legend=dict(orientation="h", yanchor="top", y=-0.22, xanchor="left", x=0, font=dict(size=11)),
        margin=dict(t=30, l=8, r=8, b=8), yaxis_title=ytitle,
        xaxis=dict(tickmode="array", tickvals=labels[::step], tickangle=-45),
    )
    return fig


def mark_today(fig, label):
    fig.add_shape(type="line", x0=label, x1=label, y0=0, y1=1, yref="paper",
                  line=dict(color="#f2f4f8", width=1, dash="dot"))
    fig.add_annotation(x=label, y=1, yref="paper", text="hoje", showarrow=False, yshift=10,
                       font=dict(color="#f2f4f8", size=11))


PLOT_CFG = {"displayModeBar": False}

st.markdown(
    """
    <style>
    .stApp { background-color: #0e1117; }
    .block-container { padding-top: 2.2rem; }
    .kpi-grid, .day-grid { display: grid; gap: 10px; margin: 6px 0 14px 0; }
    .kpi-grid { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
    .day-grid { grid-template-columns: repeat(auto-fit, minmax(108px, 1fr)); }
    .kpi { background:#161b26; border:1px solid #262d3d; border-radius:10px; padding:14px 16px; }
    .kpi-label { font-size:.74rem; color:#8b93a7; text-transform:uppercase; letter-spacing:.04em; margin-bottom:4px; }
    .kpi-value { font-size:1.75rem; font-weight:700; line-height:1.15; }
    .kpi-delta { font-size:.88rem; font-weight:600; margin-top:3px; }
    .kpi-sub { font-size:.78rem; color:#8b93a7; margin-top:3px; }
    .insight { background:linear-gradient(135deg,#161b26 0%,#1c2a1f 100%); border-left:4px solid #4fd18c;
               border-radius:10px; padding:18px 22px; margin:10px 0 16px 0; line-height:1.55; }
    .insight.alert { border-left-color:#ff6b6b; background:linear-gradient(135deg,#161b26 0%,#2a1c1f 100%); }
    .insight.warn { border-left-color:#ffb454; background:linear-gradient(135deg,#161b26 0%,#2a251c 100%); }
    .insight .headline { font-size:1.05rem; font-weight:700; margin-bottom:8px; }
    .day { background:#161b26; border:1px solid #262d3d; border-radius:10px; padding:10px 6px; text-align:center; }
    .day .n { font-weight:600; color:#f2f4f8; font-size:.9rem; }
    .day .i { font-size:1.6rem; margin:4px 0; }
    .day .t { color:#f2f4f8; font-size:.98rem; } .day .t .mn { color:#8b93a7; }
    .day .r { font-size:.76rem; color:#6fb1ff; margin-top:2px; }
    .day.hot { border-color:#ff6b6b; }
    @media (max-width: 640px) {
      h1 { font-size: 1.45rem !important; }
      .kpi-grid { grid-template-columns: repeat(2, 1fr); }
      .kpi { padding: 12px 12px; }
      .kpi-value { font-size: 1.3rem; }
      .block-container { padding-left: .9rem; padding-right: .9rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("🌱 Clima x Agro")
st.sidebar.caption("Monitoramento climático para leitura de safra — soja e milho")

city_names = list(CITIES.keys())
qp_city = st.query_params.get("cidade")
default_idx = city_names.index(qp_city) if qp_city in city_names else 0
city_name = st.sidebar.selectbox("Polo produtor", city_names, index=default_idx)
st.query_params["cidade"] = city_name  # permite compartilhar o link já na cidade escolhida
city = CITIES[city_name]

past_days = st.sidebar.radio("Janela retroativa (dias)", [15, 30, 60, 90], index=1, horizontal=True)

with st.sidebar.expander("⚙️ Parâmetros agronômicos (indicativos)"):
    planting_mm = st.slider("Chuva mín. p/ liberar plantio (mm em 15 dias)", 20, 150, 60, 5)
    heat_c = st.slider("Limite de calor (°C)", 30, 40, 35, 1)
    alert_days = st.slider("Alerta de veranico (dias secos)", 5, 20, 10, 1)
params = {"planting_mm": planting_mm, "heat_c": heat_c, "alert_days": alert_days}

if st.sidebar.button("🔄 Atualizar dados"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    f"Dados: [Open-Meteo](https://open-meteo.com) (gratuito, sem chave). Normal climatológica = média dos "
    f"últimos {HIST_YEARS} anos. Os parâmetros acima são heurísticas ajustáveis, não recomendação agronômica."
)

# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------

today = datetime.now(ZoneInfo(city["tz"])).date()  # fuso da cidade (o servidor da nuvem roda em UTC)

with st.spinner(f"Buscando dados climáticos para {city_name}..."):
    try:
        data = build_data(city, today, past_days)
    except Exception as e:
        st.error(f"Não foi possível consultar a API Open-Meteo agora ({e}). Tente atualizar em instantes.")
        st.stop()

if data["current"] is None or data["current"]["precipitation_sum"].isna().all():
    st.error("A API não retornou dados para este período. Tente atualizar em instantes.")
    st.stop()

dates, cur, P, F = data["dates"], data["current"], data["past_days"], FORECAST_DAYS
m = compute_metrics(data, params)
A = m["arrays"]
n_years_hist = len(data["years"])
phase_label, sensitive, planting_season = get_agro_phase(today)
labels = [d.strftime("%d/%m") for d in dates]
today_label = today.strftime("%d/%m")
ly_year = today.year - 1

# ---------------------------------------------------------------------------
# Cabeçalho
# ---------------------------------------------------------------------------

st.title(f"📍 {city_name} — Clima para Agro")
st.caption(
    f"{P} dias observados + {FORECAST_DAYS} de previsão · comparado a {ly_year} e à média de "
    f"{n_years_hist} anos · Fase típica: **{phase_label}** · "
    f"Dados de {data['fetched_at'].strftime('%d/%m %H:%M')} (Brasília)"
)

# ---------------------------------------------------------------------------
# Previsão — cards responsivos
# ---------------------------------------------------------------------------

st.subheader(f"Previsão dos próximos {FORECAST_DAYS} dias")
cards = []
for i in range(P, P + F):
    row, d = cur.iloc[i], dates[i]
    icon, desc = WEATHER_CODES.get(int(row["weathercode"]) if pd.notna(row["weathercode"]) else -1, ("❓", "N/D"))
    prob = row["precipitation_probability_max"]
    prob_txt = f"{prob:.0f}%" if pd.notna(prob) else "–"
    mm = row["precipitation_sum"]
    mm_txt = f" · {mm:.0f} mm" if pd.notna(mm) and mm >= 1 else ""
    hot = " hot" if pd.notna(row["temperature_2m_max"]) and row["temperature_2m_max"] >= heat_c else ""
    cards.append(
        f'<div class="day{hot}"><div class="n">{WEEKDAY_PT[d.weekday()]} {d.strftime("%d/%m")}</div>'
        f'<div class="i">{icon}</div>'
        f'<div class="t"><b>{row["temperature_2m_max"]:.0f}°</b> / <span class="mn">{row["temperature_2m_min"]:.0f}°</span></div>'
        f'<div class="r">💧 {prob_txt}{mm_txt}</div><div class="r">{desc}</div></div>'
    )
st.markdown(f'<div class="day-grid">{"".join(cards)}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

st.subheader("Painel de Insights Agro")

d_ly = delta_txt(m["rain_past"], m["rain_past_ly"])
dev_sign = (m["rain_past"] - m["rain_past_ly"]) if m["rain_past_ly"] is not None and m["rain_past"] is not None else 0
rain_tone = ("good" if dev_sign >= 0 else "warn") if sensitive else "neutral"
d_hist = delta_txt(m["rain_past"], m["rain_past_hist"])

plant_tone = {"Favorável": "good", "Parcial": "warn", "Insuficiente": "bad", None: "neutral"}[m["plant_status"]]
if not planting_season:
    plant_tone = "neutral"
plant_sub = f"mín. configurado: {planting_mm} mm" + ("" if planting_season else " · fora da época de plantio")
rain15_vs_hist = delta_txt(m["rain15"], m["rain15_hist"])

dry_tone = "bad" if (sensitive and m["dry_streak"] >= alert_days) else "neutral"
dry_sub = f"ano passado: {m['dry_streak_ly']} dias" if m["dry_streak_ly"] is not None else "ano passado: N/D"
if m["dry_projected"] > m["dry_streak"]:
    dry_sub += f" · projeção: {m['dry_projected']} dias"

t_delta = None
if m["tmax_fwd"] is not None and m["tmax_fwd_ly"] is not None:
    t_delta = f"{m['tmax_fwd'] - m['tmax_fwd_ly']:+.1f}°C vs {ly_year}"
heat_tone = "bad" if m["heat_days"] >= 3 else ("warn" if m["heat_days"] >= 1 else "neutral")

rank_txt = f"{m['rank']}º / {m['rank_total']}" if m["rank"] else "N/D"

kpis = [
    kpi_card(f"Chuva acumulada ({P}d)", fmt_mm(m["rain_past"]), d_ly and f"{d_ly} vs {ly_year}",
             d_hist and f"{d_hist} vs média {n_years_hist}a", tone="neutral", delta_tone=rain_tone),
    kpi_card("Janela de plantio (15d)", fmt_mm(m["rain15"]), m["plant_status"] or "N/D",
             plant_sub + (f" · média: {m['rain15_hist']:.0f} mm" if m["rain15_hist"] is not None else ""),
             delta_tone=plant_tone),
    kpi_card(f"Chuva prevista ({FORECAST_DAYS}d)", fmt_mm(m["rain_fwd"]),
             m["rain_fwd_ly"] is not None and f"{m['rain_fwd'] - m['rain_fwd_ly']:+.0f} mm vs {ly_year}" or None,
             m["rain_fwd_hist"] is not None and f"média {n_years_hist}a: {m['rain_fwd_hist']:.0f} mm" or None),
    kpi_card("Veranico atual", f"{m['dry_streak']} dias", None, dry_sub, tone=dry_tone),
    kpi_card(f"Dias ≥ {heat_c}°C (prev.)", f"{m['heat_days']} de {FORECAST_DAYS}", t_delta,
             f"Tmax média prevista: {m['tmax_fwd']:.1f}°C" if m["tmax_fwd"] is not None else None,
             tone=heat_tone, delta_tone="bad" if (t_delta and not t_delta.startswith("-")) else "cold"),
    kpi_card(f"Ranking de chuva ({P}d)", rank_txt, None,
             f"1º = mais chuvoso de {m['rank_total']} anos" if m["rank"] else None),
]
st.markdown(f'<div class="kpi-grid">{"".join(kpis)}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Insight textual com semáforo
# ---------------------------------------------------------------------------

lines, level = [], 0  # 0 ok · 1 atenção · 2 alerta

if m["rain_past_ly"] is not None:
    p = pct(m["rain_past"], m["rain_past_ly"])
    p_txt = f" ({p:+.0f}%)" if p is not None else ""
    lines.append(
        f"🌧️ Nos últimos **{P} dias**, **{city_name}** acumulou **{m['rain_past']:.0f} mm**, contra "
        f"**{m['rain_past_ly']:.0f} mm** no mesmo período de {ly_year}{p_txt}."
    )
if m["rain_past_hist"] is not None:
    lines.append(
        f"📊 Média dos últimos {n_years_hist} anos para essas datas: **{m['rain_past_hist']:.0f} mm** "
        f"— este ano está {delta_txt(m['rain_past'], m['rain_past_hist'])} em relação a ela."
        + (f" Este ano ocupa a **{m['rank']}ª posição** entre {m['rank_total']} anos (1ª = mais chuvoso)." if m["rank"] else "")
    )
    if sensitive and m["rain_past_hist"] >= 20:
        p = pct(m["rain_past"], m["rain_past_hist"])
        if p is not None and p <= -30:
            level = max(level, 1)
            lines.append(f"⚠️ Chuva **{abs(p):.0f}% abaixo da média** durante a fase de **{phase_label.lower()}**.")
        elif p is not None and p >= 30:
            lines.append(f"✅ Chuva **{p:.0f}% acima da média** durante a fase de **{phase_label.lower()}** — boa umidade.")

if planting_season and m["plant_status"]:
    if m["plant_status"] == "Insuficiente":
        level = max(level, 1)  # heurística ajustável: atenção, não alerta máximo
        lines.append(
            f"🔴 **Janela de plantio:** só **{m['rain15']:.0f} mm** em 15 dias (mín. configurado: {planting_mm} mm) — "
            f"umidade do solo **insuficiente**; plantio tende a aguardar novas chuvas."
        )
    elif m["plant_status"] == "Parcial":
        level = max(level, 1)
        lines.append(
            f"🟡 **Janela de plantio:** **{m['rain15']:.0f} mm** em 15 dias — **parcial** "
            f"(mín. configurado: {planting_mm} mm). Acompanhe a previsão antes de avançar."
        )
    else:
        lines.append(f"🟢 **Janela de plantio:** **{m['rain15']:.0f} mm** em 15 dias — umidade **favorável**.")

if sensitive and m["dry_streak"] >= alert_days:
    level = 2
    ly_txt = f" (no ano passado, na mesma data: {m['dry_streak_ly']} dias)" if m["dry_streak_ly"] is not None else ""
    lines.append(
        f"⚠️ **Veranico:** **{m['dry_streak']} dias seguidos** sem chuva relevante (< {DRY_MM:.0f} mm/dia){ly_txt}. "
        f"Risco para germinação/desenvolvimento na fase de **{phase_label.lower()}**."
    )
elif not sensitive and m["dry_streak"] >= alert_days:
    lines.append(f"☀️ **{m['dry_streak']} dias** sem chuva relevante — esperado na **{phase_label.lower()}**, sem alerta.")

if m["dry_projected"] > m["dry_streak"]:
    end_d = dates[P + (m["dry_projected"] - m["dry_streak"]) - 1]
    lines.append(
        f"📅 Se a previsão se confirmar, o período seco chega a **{m['dry_projected']} dias** "
        f"até **{WEEKDAY_PT[end_d.weekday()]} {end_d.strftime('%d/%m')}**."
    )

if m["rain_fwd_ly"] is not None:
    lines.append(
        f"🔮 Próximos **{FORECAST_DAYS} dias**: **{m['rain_fwd']:.0f} mm** previstos "
        f"(mesmo período de {ly_year}: {m['rain_fwd_ly']:.0f} mm)."
    )

if m["heat_days"] >= 1:
    if m["heat_days"] >= 3 and sensitive:
        level = max(level, 1)
    lines.append(f"🔥 **{m['heat_days']} dia(s)** com máxima ≥ **{heat_c}°C** na previsão — atenção a estresse térmico.")

headline = {
    0: ("🟢 Condições dentro do esperado", ""),
    1: ("🟡 Pontos de atenção", " warn"),
    2: ("🔴 Alerta", " alert"),
}[level]
body = "<br>".join(md_bold_to_html(x) for x in lines) or "Sem desvios relevantes para o período."
st.markdown(
    f'<div class="insight{headline[1]}"><div class="headline">{headline[0]}</div>{body}</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Gráficos
# ---------------------------------------------------------------------------

tab_cum, tab_year, tab_temp, tab_daily, tab_cmp = st.tabs(
    ["💧 Chuva acumulada", "📊 Por ano", "🌡️ Temperatura", "🌧️ Chuva diária", "🗺️ Comparar polos"]
)

with tab_cum:
    fig = go.Figure()
    if A["hist_rain"] is not None:
        cum_h = A["hist_rain"].fillna(0).cumsum(axis=0)
        fig.add_trace(go.Scatter(x=labels, y=cum_h.max(axis=1), mode="lines", line=dict(width=0),
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=labels, y=cum_h.min(axis=1), mode="lines", line=dict(width=0),
                                 fill="tonexty", fillcolor="rgba(79,140,255,0.14)",
                                 name=f"Faixa {n_years_hist} anos (mín–máx)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=labels, y=cum_h.mean(axis=1), mode="lines", name=f"Média {n_years_hist} anos",
                                 line=dict(color="#4f8cff", width=2, dash="dash")))
    if A["rain_ly"] is not None:
        fig.add_trace(go.Scatter(x=labels, y=np.nancumsum(A["rain_ly"]), mode="lines", name=str(ly_year),
                                 line=dict(color="#8b93a7", width=2, dash="dot")))
    fig.add_trace(go.Scatter(x=labels, y=np.nancumsum(A["rain_now"]), mode="lines",
                             name=f"{today.year} (obs. + previsão)", line=dict(color="#4fd18c", width=3)))
    mark_today(fig, today_label)
    st.plotly_chart(style_fig(fig, labels, 460, "Chuva acumulada (mm)"), width="stretch", config=PLOT_CFG)
    st.caption("A faixa azul mostra o intervalo entre o ano mais seco e o mais chuvoso da série para cada dia.")

with tab_year:
    ys = m["year_sums"]
    if len(ys) and m["rain_past"] is not None:
        yrs_x = [today.year - int(y) for y in ys.index] + [today.year]
        yrs_y = list(ys.values) + [m["rain_past"]]
        order = np.argsort(yrs_x)
        yrs_x = [str(yrs_x[i]) for i in order]
        yrs_y = [yrs_y[i] for i in order]
        colors = ["#4fd18c" if x == str(today.year) else "#3b4560" for x in yrs_x]
        fig = go.Figure(go.Bar(x=yrs_x, y=yrs_y, marker_color=colors, text=[f"{v:.0f}" for v in yrs_y],
                               textposition="outside", name="Chuva na janela"))
        fig.add_hline(y=float(ys.mean()), line_dash="dash", line_color="#4f8cff",
                      annotation_text=f"média {float(ys.mean()):.0f} mm", annotation_position="top left")
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          height=420, dragmode=False, showlegend=False, margin=dict(t=30, l=8, r=8, b=8),
                          yaxis_title=f"Chuva em {P} dias (mm)", xaxis=dict(type="category"))
        st.plotly_chart(fig, width="stretch", config=PLOT_CFG)
        st.caption(f"Chuva acumulada na mesma janela de {P} dias (até hoje) em cada ano. Este ano em verde.")
    else:
        st.info("Histórico indisponível no momento.")

with tab_temp:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=A["tmax_now"], mode="lines", name=f"Máx. {today.year}",
                             line=dict(color="#ff6b6b", width=2.5)))
    fig.add_trace(go.Scatter(x=labels, y=A["tmin_now"], mode="lines", name=f"Mín. {today.year}",
                             line=dict(color="#4f8cff", width=2.5)))
    if A["tmax_ly"] is not None:
        fig.add_trace(go.Scatter(x=labels, y=A["tmax_ly"], mode="lines", name=f"Máx. {ly_year}",
                                 line=dict(color="#ff6b6b", width=1.5, dash="dot")))
    if A["hist_tmax_avg"] is not None:
        fig.add_trace(go.Scatter(x=labels, y=A["hist_tmax_avg"], mode="lines", name=f"Máx. média {n_years_hist}a",
                                 line=dict(color="#8b93a7", width=1.5, dash="dash")))
    fig.add_hline(y=heat_c, line_dash="dot", line_color="#ffb454", annotation_text=f"{heat_c}°C",
                  annotation_position="bottom right")
    mark_today(fig, today_label)
    st.plotly_chart(style_fig(fig, labels, 440, "Temperatura (°C)"), width="stretch", config=PLOT_CFG)

with tab_daily:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=A["rain_now"], name=str(today.year), marker_color="#4fd18c"))
    if A["rain_ly"] is not None:
        fig.add_trace(go.Bar(x=labels, y=A["rain_ly"], name=str(ly_year), marker_color="#8b93a7"))
    fig.update_layout(barmode="group")
    mark_today(fig, today_label)
    st.plotly_chart(style_fig(fig, labels, 420, "Precipitação (mm)"), width="stretch", config=PLOT_CFG)

with tab_cmp:
    rows = []
    load_cmp = st.session_state.get("cmp_loaded", False)
    if not load_cmp:
        st.write(
            "Compara os 5 polos lado a lado (chuva acumulada, ranking, plantio, veranico, calor). "
            "No primeiro carregamento leva cerca de 30 segundos, pois consulta o histórico de cada polo."
        )
        if st.button("Carregar comparação dos 5 polos"):
            st.session_state["cmp_loaded"] = True
            load_cmp = True
    with st.spinner("Comparando os polos produtores..."):
        for name, c in (CITIES.items() if load_cmp else []):
            try:
                t_c = datetime.now(ZoneInfo(c["tz"])).date()
                d_c = build_data(c, t_c, past_days)
                mc = compute_metrics(d_c, params)
            except Exception:
                continue
            p_ly, p_h = pct(mc["rain_past"], mc["rain_past_ly"]), pct(mc["rain_past"], mc["rain_past_hist"])
            rows.append({
                "Polo": name,
                f"Chuva {past_days}d (mm)": mc["rain_past"],
                f"vs {ly_year}": p_ly,
                "vs média": p_h,
                "Rank": f"{mc['rank']}º/{mc['rank_total']}" if mc["rank"] else "N/D",
                "Chuva 15d (mm)": mc["rain15"],
                "Plantio": mc["plant_status"] or "N/D",
                "Veranico (dias)": mc["dry_streak"],
                f"Prev. {FORECAST_DAYS}d (mm)": mc["rain_fwd"],
                f"Dias ≥{heat_c}°C": mc["heat_days"],
            })
    if rows:
        cmp_df = pd.DataFrame(rows)
        st.dataframe(
            cmp_df, hide_index=True, width="stretch",
            column_config={
                f"Chuva {past_days}d (mm)": st.column_config.NumberColumn(format="%.0f"),
                f"vs {ly_year}": st.column_config.NumberColumn(format="%+.0f%%"),
                "vs média": st.column_config.NumberColumn(format="%+.0f%%"),
                "Chuva 15d (mm)": st.column_config.NumberColumn(format="%.0f"),
                f"Prev. {FORECAST_DAYS}d (mm)": st.column_config.NumberColumn(format="%.0f"),
            },
        )
        fig = go.Figure(go.Bar(x=cmp_df["Polo"], y=cmp_df[f"Chuva {past_days}d (mm)"],
                               marker_color=["#4fd18c" if p == city_name else "#3b4560" for p in cmp_df["Polo"]],
                               text=[f"{v:.0f}" if pd.notna(v) else "" for v in cmp_df[f"Chuva {past_days}d (mm)"]],
                               textposition="outside"))
        fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          height=340, dragmode=False, showlegend=False, margin=dict(t=30, l=8, r=8, b=8),
                          yaxis_title=f"Chuva em {past_days} dias (mm)")
        st.plotly_chart(fig, width="stretch", config=PLOT_CFG)
    elif load_cmp:
        st.info("Não foi possível carregar a comparação agora (limite da API). Tente novamente em alguns minutos.")

# ---------------------------------------------------------------------------
# Dados brutos + download
# ---------------------------------------------------------------------------

table = pd.DataFrame({
    "Data": [d.isoformat() for d in dates],
    "Tmax atual (°C)": A["tmax_now"],
    "Tmin atual (°C)": A["tmin_now"],
    "Chuva atual (mm)": A["rain_now"],
    f"Chuva {ly_year} (mm)": A["rain_ly"] if A["rain_ly"] is not None else np.nan,
    f"Chuva média {n_years_hist}a (mm)": A["hist_rain_avg"] if A["hist_rain_avg"] is not None else np.nan,
    "Chuva acumulada atual (mm)": np.nancumsum(A["rain_now"]),
}).round(1)

with st.expander("📄 Dados brutos"):
    st.dataframe(table, hide_index=True, width="stretch")
st.download_button(
    "⬇️ Baixar dados (CSV)",
    table.to_csv(index=False).encode("utf-8-sig"),
    file_name=f"clima_agro_{city_name.split(' (')[0].lower().replace(' ', '_')}_{today.isoformat()}.csv",
    mime="text/csv",
)

st.caption(
    "Dados: Open-Meteo.com. As fases do calendário agrícola e os limiares (plantio, calor, veranico) são "
    "aproximações indicativas e ajustáveis para o Centro-Oeste — não substituem recomendação agronômica local. "
    "Painel de caráter analítico/informativo."
)
