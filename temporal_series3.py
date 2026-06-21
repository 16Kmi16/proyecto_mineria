import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, median_absolute_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent
SENTINEL: float = -1.0


def limpiar_sentinels(df: pd.DataFrame, sentinel: float = SENTINEL) -> pd.DataFrame:
    cols_numericas = df.select_dtypes(include="number").columns
    df[cols_numericas] = df[cols_numericas].replace(sentinel, np.nan)
    return df

VENTANA_MIN: int = 60
PASO_MIN: int = 5
HORIZONTE_MIN: int = 120

def cargar_dataset(nombre_archivo: str) -> pd.DataFrame:
    ruta = BASE_DIR / nombre_archivo
    df = pd.read_csv(ruta, parse_dates=["TIME"])
    df = limpiar_sentinels(df)
    return df.sort_values(["source", "TIME"]).reset_index(drop=True)


def detectar_sensores_disponibles(*dfs: pd.DataFrame) -> list[int]:
    cols = set().union(*(set(df.columns) for df in dfs))
    return sorted({int(m.group(1)) for c in cols if (m := re.match(r"ox_s(\d+)", c))})


def calcular_tiempo_hasta_setpoint(
    df: pd.DataFrame,
    sensor: int = 1,
    horizonte_min: int = 360,
    stop_on_target: bool = False,
    max_horizonte_min: int = 1440,
) -> pd.DataFrame:
    ox_col = f"ox_s{sensor}"
    sp_col = f"sp_s{sensor}"

    faltantes = [col for col in ["suma_psa", ox_col, sp_col] if col not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas: {faltantes}")

    trabajo = df[["source", "TIME", "suma_psa", ox_col, sp_col]].copy()
    trabajo["psa_on"] = trabajo["suma_psa"].fillna(0) > 0

    eventos = []

    for source, chunk in trabajo.groupby("source", sort=False):
        chunk = chunk.reset_index(drop=True)
        inicio_mask = chunk["psa_on"] & ~chunk["psa_on"].shift(fill_value=False)

        for idx in chunk.index[inicio_mask]:
            inicio = chunk.loc[idx, "TIME"]
            ox_inicio = chunk.loc[idx, ox_col]

            if stop_on_target:
                ventana_fin = inicio + pd.Timedelta(minutes=max_horizonte_min)
            else:
                ventana_fin = inicio + pd.Timedelta(minutes=horizonte_min)

            ventana = chunk[
                (chunk["TIME"] >= inicio) & (chunk["TIME"] <= ventana_fin)
            ][["TIME", ox_col, sp_col]].copy()

            setpoint = ventana[sp_col].dropna().median()
            if pd.isna(setpoint) or setpoint <= 0:
                continue
            if pd.isna(ox_inicio) or ox_inicio >= setpoint:
                continue

            ventana = ventana[ventana[ox_col].notna() & (ventana[ox_col] > 0)]
            if ventana.empty:
                continue

            ventana["target"] = ventana[sp_col].fillna(setpoint)
            cruce = ventana[ventana[ox_col] >= ventana["target"]]
            minutos = (
                np.nan
                if cruce.empty
                else (cruce.iloc[0]["TIME"] - inicio).total_seconds() / 60
            )

            eventos.append(
                {
                    "source": source,
                    "inicio": inicio,
                    "sensor": sensor,
                    "setpoint": float(setpoint),
                    "ox_inicio": float(ox_inicio),
                    "minutos_hasta_setpoint": minutos,
                }
            )

    return pd.DataFrame(eventos)


def construir_features_ventana(
    df: pd.DataFrame,
    eventos: pd.DataFrame,
    sensor: int,
    ventana_min: int = VENTANA_MIN,
    paso_min: int = PASO_MIN,
) -> pd.DataFrame:
    ox_col = f"ox_s{sensor}"
    sp_col = f"sp_s{sensor}"

    registros = []

    for _, ev in eventos.iterrows():
        source = ev["source"]
        inicio = ev["inicio"]

        chunk = df[df["source"] == source].sort_values("TIME")
        ventana = chunk[
            (chunk["TIME"] >= inicio - pd.Timedelta(minutes=ventana_min))
            & (chunk["TIME"] < inicio)
        ].copy()

        if ventana.empty or ox_col not in ventana.columns:
            continue
        ventana = ventana.set_index("TIME")
        ventana_rs = ventana[[ox_col, sp_col, "suma_psa"]].resample(
            f"{paso_min}min"
        ).mean()

        ventana_rs[ox_col] = ventana_rs[ox_col].where(ventana_rs[ox_col] > 0)
        ventana_rs[sp_col] = ventana_rs[sp_col].where(ventana_rs[sp_col] > 0)

        ox = ventana_rs[ox_col].dropna()
        if len(ox) < 3:
            continue

        n_pasos_total = len(ventana_rs)
        cobertura_ox = len(ox) / n_pasos_total if n_pasos_total > 0 else 0.0

        sp_vals = ventana_rs[sp_col].dropna()
        psa_vals = (ventana_rs["suma_psa"].fillna(0) > 0).astype(int)

        x = np.arange(len(ox))
        slope = float(np.polyfit(x, ox.values, 1)[0]) if len(ox) >= 2 else 0.0

        psa_diff = psa_vals.diff().fillna(0)
        n_arranques = int((psa_diff > 0).sum())

        feat = {
            "source": source,
            "inicio": inicio,
            "sensor": sensor,
            "ox_mean": float(ox.mean()),
            "ox_std": float(ox.std(ddof=0)),
            "ox_min": float(ox.min()),
            "ox_max": float(ox.max()),
            "ox_last": float(ox.iloc[-1]),
            "ox_slope": slope,
            # --- setpoint y brecha ---
            "sp_mean": float(sp_vals.mean()) if not sp_vals.empty else np.nan,
            "gap_ox_sp": float(ev["setpoint"] - ev["ox_inicio"]),
            # --- PSA en la ventana ---
            "psa_ratio": float(psa_vals.mean()),
            "n_arranques_previos": n_arranques,
            # --- calidad del dato en la ventana ---
            "cobertura_ox": cobertura_ox,
            # --- temporalidad ---
            "hora_del_dia": inicio.hour + inicio.minute / 60,
            "dia_semana": inicio.dayofweek,
            # --- target ---
            "minutos_hasta_setpoint": ev["minutos_hasta_setpoint"],
        }
        registros.append(feat)

    df_feat = pd.DataFrame(registros).dropna(subset=["minutos_hasta_setpoint"])
    return df_feat


# ---------------------------------------------------------------------------
# Entrenamiento y evaluación del Random Forest
# ---------------------------------------------------------------------------
FEATURE_COLS = [
    "ox_mean", "ox_std", "ox_min", "ox_max", "ox_last", "ox_slope",
    "sp_mean", "gap_ox_sp",
    "psa_ratio", "n_arranques_previos",
    "cobertura_ox",
    "hora_del_dia", "dia_semana",
]


def entrenar_rf(
    df_feat: pd.DataFrame,
    n_estimators: int = 200,
    random_state: int = 42,
) -> tuple[RandomForestRegressor, StandardScaler, dict]:
    """
    Entrena un Random Forest con GroupShuffleSplit (grupos = source)
    para evitar data-leakage entre centros de cultivo.

    Retorna: modelo entrenado, scaler, y métricas de validación.
    """
    df_clean = df_feat.dropna(subset=FEATURE_COLS).copy()
    if len(df_clean) < 10:
        raise ValueError(
            f"Muy pocos eventos para entrenar ({len(df_clean)}). "
            "Prueba con más datos o un horizonte mayor."
        )

    X = df_clean[FEATURE_COLS].values
    y = np.log1p(df_clean["minutos_hasta_setpoint"].values)
    groups = df_clean["source"].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=random_state)
    train_idx, test_idx = next(splitter.split(X_scaled, y, groups))

    X_train, X_test = X_scaled[train_idx], X_scaled[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]

    rf = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=12,
        min_samples_leaf=4,
        n_jobs=-1,
        random_state=random_state,
    )
    rf.fit(X_train, y_train)

    y_pred = rf.predict(X_test)
    metricas = {
        "mae": mean_absolute_error(y_test, y_pred),
        "medae": median_absolute_error(y_test, y_pred),
        "r2": r2_score(y_test, y_pred),
        "n_train": len(train_idx),
        "n_test": len(test_idx),
    }

    return rf, scaler, metricas


def predecir(
    rf: RandomForestRegressor,
    scaler: StandardScaler,
    df_feat: pd.DataFrame,
) -> pd.DataFrame:
    """Agrega columna `pred_minutos` al dataframe de features."""
    df_out = df_feat.dropna(subset=FEATURE_COLS).copy()
    X = scaler.transform(df_out[FEATURE_COLS].values)
    df_out["pred_minutos"] = np.expm1(rf.predict(X))
    return df_out


# ---------------------------------------------------------------------------
# Visualización
# ---------------------------------------------------------------------------
def graficar_importancias(rf: RandomForestRegressor) -> None:
    imp = pd.Series(rf.feature_importances_, index=FEATURE_COLS).sort_values()
    fig, ax = plt.subplots(figsize=(8, 5))
    imp.plot.barh(ax=ax, color="steelblue")
    ax.set_title("Importancia de features — Random Forest")
    ax.set_xlabel("Importancia media (impureza)")
    ax.axvline(0, color="black", linewidth=0.8)
    plt.tight_layout()
    plt.show()


def graficar_pred_vs_real(df_pred: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7, 6))
    ax.scatter(
        df_pred["minutos_hasta_setpoint"],
        df_pred["pred_minutos"],
        alpha=0.4,
        s=18,
        color="tab:blue",
    )
    lim = max(
        df_pred["minutos_hasta_setpoint"].max(),
        df_pred["pred_minutos"].max(),
    )
    ax.plot([0, lim], [0, lim], "r--", linewidth=1.2, label="Predicción perfecta")
    ax.set_xlabel("Real (min)")
    ax.set_ylabel("Predicho (min)")
    ax.set_title("Predicho vs Real — minutos hasta setpoint")
    ax.legend()
    ax.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.show()


def graficar_evento(
    df: pd.DataFrame,
    df_pred: pd.DataFrame,
    sensor: int = 1,
    indice_evento: int = 0,
    ventana_pre: int = 30,
    ventana_post: int = 180,
) -> None:
    if df_pred.empty:
        print("No hay predicciones disponibles para graficar.")
        return

    evento = df_pred.iloc[indice_evento]
    source = evento["source"]
    inicio = evento["inicio"]
    ox_col = f"ox_s{sensor}"
    sp_col = f"sp_s{sensor}"

    chunk = df[df["source"] == source].sort_values("TIME")
    ventana = chunk[
        (chunk["TIME"] >= inicio - pd.Timedelta(minutes=ventana_pre))
        & (chunk["TIME"] <= inicio + pd.Timedelta(minutes=ventana_post))
    ].copy()
    ventana["psa_on"] = ventana["suma_psa"].fillna(0) > 0

    pred_min = evento.get("pred_minutos", np.nan)
    real_min = evento.get("minutos_hasta_setpoint", np.nan)

    fig, ax1 = plt.subplots(figsize=(14, 5))
    ax1.plot(ventana["TIME"], ventana[ox_col], label=ox_col, color="tab:blue", linewidth=2)
    ax1.plot(ventana["TIME"], ventana[sp_col], label=sp_col, color="tab:orange", linestyle="--", linewidth=2)
    ax1.axvline(inicio, color="tab:red", linestyle=":", linewidth=2, label="Inicio PSA")

    if not np.isnan(pred_min):
        t_pred = inicio + pd.Timedelta(minutes=pred_min)
        ax1.axvline(t_pred, color="tab:purple", linestyle="-.", linewidth=2,
                    label=f"RF pred: {pred_min:.1f} min")

    titulo = f"{source} | Sensor {sensor}"
    if not np.isnan(real_min):
        titulo += f" | Real: {real_min:.1f} min"
    if not np.isnan(pred_min):
        titulo += f" | Pred: {pred_min:.1f} min"
    ax1.set_ylabel("Oxígeno / Setpoint")
    ax1.set_title(titulo)
    ax1.grid(True, alpha=0.25)

    ax2 = ax1.twinx()
    ax2.step(ventana["TIME"], ventana["psa_on"].astype(int), where="post",
             color="tab:green", alpha=0.35, label="PSA ON")
    ax2.set_ylabel("Estado PSA")
    ax2.set_yticks([0, 1])
    ax2.set_ylim(-0.1, 1.1)

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")
    plt.tight_layout()
    plt.show()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Predicción de minutos hasta setpoint de oxígeno "
            "usando Random Forest con ventana deslizante."
        )
    )
    parser.add_argument("--horizonte-min", type=int, default=360,
                        help="Ventana máxima para calcular el label (default 360 min).")
    parser.add_argument("--ventana-min", type=int, default=VENTANA_MIN,
                        help=f"Lookback de la ventana deslizante de features (default {VENTANA_MIN} min).")
    parser.add_argument("--paso-min", type=int, default=PASO_MIN,
                        help=f"Paso de resampleo para features (default {PASO_MIN} min).")
    parser.add_argument("--stop-on-target", action="store_true",
                        help="Observar hasta que el oxígeno alcanza el setpoint.")
    parser.add_argument("--max-horizonte-min", type=int, default=1440,
                        help="Tope máximo en minutos con --stop-on-target (default 1440).")
    parser.add_argument("--sin-grafico", action="store_true",
                        help="No mostrar gráficos.")
    parser.add_argument("--n-estimators", type=int, default=200,
                        help="Número de árboles del Random Forest (default 200).")
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Cargar datos
    # ------------------------------------------------------------------
    df_3comp = cargar_dataset("dataset_3comp_limpio.csv")
    df_4comp = cargar_dataset("dataset_4comp_limpio.csv")
    df_todo = pd.concat([df_3comp, df_4comp], ignore_index=True)

    # Reporte de limpieza: cuántos -1 fueron reemplazados por NaN
    def contar_sentinels(ruta: str, sentinel: float = SENTINEL) -> int:
        raw = pd.read_csv(BASE_DIR / ruta)
        nums = raw.select_dtypes(include="number")
        return int((nums == sentinel).sum().sum())

    n_s1 = contar_sentinels("dataset_3comp_limpio.csv")
    n_s2 = contar_sentinels("dataset_4comp_limpio.csv")
    print(f"[limpieza] dataset_3comp: {n_s1:,} valores -1 → NaN")
    print(f"[limpieza] dataset_4comp: {n_s2:,} valores -1 → NaN")
    print(f"[limpieza] Total         : {n_s1 + n_s2:,} centinelas eliminados\n")

    sensor_nums = detectar_sensores_disponibles(df_3comp, df_4comp)
    if not sensor_nums:
        print("No se encontraron columnas ox_sN en los datasets.")
        return

    # ------------------------------------------------------------------
    # 2. Calcular labels (tiempo real hasta setpoint)
    # ------------------------------------------------------------------
    eventos_lista = []
    for sensor in sensor_nums:
        for df_src, grupo in [(df_3comp, "3comp"), (df_4comp, "4comp")]:
            ev = calcular_tiempo_hasta_setpoint(
                df_src,
                sensor=sensor,
                horizonte_min=args.horizonte_min,
                stop_on_target=args.stop_on_target,
                max_horizonte_min=args.max_horizonte_min,
            )
            if not ev.empty:
                eventos_lista.append(ev.assign(grupo=grupo))

    if not eventos_lista:
        print("No se encontraron eventos válidos.")
        return

    eventos_all = pd.concat(eventos_lista, ignore_index=True)

    # ------------------------------------------------------------------
    # 3. Construir features con ventana deslizante
    # ------------------------------------------------------------------
    print(f"\n[1/3] Construyendo features (ventana={args.ventana_min} min, paso={args.paso_min} min)...")
    feats_lista = []
    for sensor in sensor_nums:
        ev_sensor = eventos_all[eventos_all["sensor"] == sensor]
        feats = construir_features_ventana(
            df_todo, ev_sensor, sensor,
            ventana_min=args.ventana_min,
            paso_min=args.paso_min,
        )
        if not feats.empty:
            feats_lista.append(feats)

    if not feats_lista:
        print("No se pudieron construir features. Revisa la longitud del histórico.")
        return

    df_feat = pd.concat(feats_lista, ignore_index=True)
    print(f"    → {len(df_feat):,} eventos con features completas.")

    # ------------------------------------------------------------------
    # 4. Entrenar Random Forest
    # ------------------------------------------------------------------
    print(f"\n[2/3] Entrenando Random Forest ({args.n_estimators} árboles)...")
    rf, scaler, metricas = entrenar_rf(df_feat, n_estimators=args.n_estimators)

    print(f"\n{'='*50}")
    print("  MÉTRICAS DE VALIDACIÓN (GroupShuffleSplit por source)")
    print(f"{'='*50}")
    print(f"  MAE   (error absoluto medio)    : {metricas['mae']:.2f} min")
    print(f"  MedAE (mediana error absoluto)  : {metricas['medae']:.2f} min")
    print(f"  R²                              : {metricas['r2']:.3f}")
    print(f"  Eventos entrenamiento           : {metricas['n_train']:,}")
    print(f"  Eventos validación              : {metricas['n_test']:,}")
    print(f"{'='*50}\n")

    # ------------------------------------------------------------------
    # 5. Predicciones sobre todos los eventos
    # ------------------------------------------------------------------
    print("[3/3] Generando predicciones...")
    df_pred = predecir(rf, scaler, df_feat)

    mediana_real = df_pred["minutos_hasta_setpoint"].median()
    mediana_pred = df_pred["pred_minutos"].median()
    print(f"  Mediana real    : {mediana_real:.2f} min")
    print(f"  Mediana predicha: {mediana_pred:.2f} min")

    # Resumen por grupo (3comp vs 4comp)
    if "grupo" in df_pred.columns:
        for g in df_pred["grupo"].unique():
            sub = df_pred[df_pred["grupo"] == g]
            print(f"\n  [{g}]")
            print(f"    Eventos: {len(sub):,}")
            print(f"    MAE    : {mean_absolute_error(sub['minutos_hasta_setpoint'], sub['pred_minutos']):.2f} min")
            print(f"    Mediana real vs pred: {sub['minutos_hasta_setpoint'].median():.1f} vs {sub['pred_minutos'].median():.1f} min")

    # ------------------------------------------------------------------
    # 6. Gráficos
    # ------------------------------------------------------------------
    if not args.sin_grafico:
        graficar_importancias(rf)
        graficar_pred_vs_real(df_pred)
        graficar_evento(df_todo, df_pred, sensor=sensor_nums[0], indice_evento=0)


if __name__ == "__main__":
    main()