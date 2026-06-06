import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent
CSV_NAME = "global_concatenado.CSV"


def cargar_global() -> pd.DataFrame:
    ruta = BASE_DIR / CSV_NAME
    df = pd.read_csv(ruta, parse_dates=["TIME"])

    columnas_basura = [c for c in ["Unnamed: 0", "Unnamed: 0.1", "Sistema"] if c in df.columns]
    if columnas_basura:
        df = df.drop(columns=columnas_basura)

    return df.sort_values(["source", "TIME"]).reset_index(drop=True)


def detectar_sensores_disponibles(df: pd.DataFrame) -> list[int]:
    return sorted({int(m.group(1)) for c in df.columns if (m := re.match(r"ox_s(\d+)", c))})


def separar_por_arquitectura(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "r_com4" not in df.columns:
        raise ValueError("No existe la columna r_com4 para separar 3comp y 4comp")

    fuentes_4comp = df[df["r_com4"].notna()]["source"].unique()
    df_4comp = df[df["source"].isin(fuentes_4comp)].copy()
    df_3comp = df[~df["source"].isin(fuentes_4comp)].copy()
    return df_3comp, df_4comp


def calcular_tiempo_hasta_setpoint(
    df: pd.DataFrame,
    sensor: int = 1,
    horizonte_min: int = 360,
    stop_on_target: bool = True,
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

            # definir tope temporal para la ventana de búsqueda
            if stop_on_target:
                ventana_fin = inicio + pd.Timedelta(minutes=max_horizonte_min)
            else:
                ventana_fin = inicio + pd.Timedelta(minutes=horizonte_min)

            ventana = chunk[(chunk["TIME"] >= inicio) & (chunk["TIME"] <= ventana_fin)][["TIME", ox_col, sp_col]].copy()
            setpoint = ventana[sp_col].dropna().median()

            if pd.isna(setpoint) or setpoint <= 0:
                continue
            if pd.isna(ox_inicio) or ox_inicio >= setpoint:
                continue

            ventana = ventana[ventana[ox_col].notna() & (ventana[ox_col] >= 0)]
            if ventana.empty:
                continue

            ventana["target"] = ventana[sp_col].fillna(setpoint)
            cruce = ventana[ventana[ox_col] >= ventana["target"]]
            minutos = np.nan if cruce.empty else (cruce.iloc[0]["TIME"] - inicio).total_seconds() / 60

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


def graficar_evento(df: pd.DataFrame, eventos: pd.DataFrame, sensor: int = 1, indice_evento: int = 0) -> None:
    eventos_validos = eventos.dropna(subset=["minutos_hasta_setpoint"])
    if eventos_validos.empty:
        print("No hay eventos válidos para graficar.")
        return

    evento = eventos_validos.iloc[indice_evento]
    source = evento["source"]
    inicio = evento["inicio"]
    ox_col = f"ox_s{sensor}"
    sp_col = f"sp_s{sensor}"

    chunk = df[df["source"] == source].sort_values("TIME")
    ventana = chunk[
        (chunk["TIME"] >= inicio - pd.Timedelta(minutes=30))
        & (chunk["TIME"] <= inicio + pd.Timedelta(minutes=180))
    ].copy()
    ventana["psa_on"] = ventana["suma_psa"].fillna(0) > 0

    fig, ax1 = plt.subplots(figsize=(14, 5))
    ax1.plot(ventana["TIME"], ventana[ox_col], label=ox_col, color="tab:blue", linewidth=2)
    ax1.plot(ventana["TIME"], ventana[sp_col], label=sp_col, color="tab:orange", linestyle="--", linewidth=2)
    ax1.axvline(inicio, color="tab:red", linestyle=":", linewidth=2, label="Inicio PSA")
    ax1.set_ylabel("Oxígeno / Setpoint")
    ax1.set_title(f"{source} | Sensor {sensor} | {evento['minutos_hasta_setpoint']:.2f} min hasta setpoint")
    ax1.grid(True, alpha=0.25)

    ax2 = ax1.twinx()
    ax2.step(ventana["TIME"], ventana["psa_on"].astype(int), where="post", color="tab:green", alpha=0.35, label="PSA ON")
    ax2.set_ylabel("Estado PSA")
    ax2.set_yticks([0, 1])
    ax2.set_ylim(-0.1, 1.1)

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")
    plt.tight_layout()
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analisis de series temporales sobre global_concatenado.CSV")
    parser.add_argument("--sensor", type=int, default=1, help="Sensor a analizar (1 a 12).")
    parser.add_argument("--horizonte-min", type=int, default=360, help="Ventana maxima para el cruce.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stop-on-target", dest="stop_on_target", action="store_true", help="Dejar de observar cuando el oxigeno alcanza el setpoint.")
    group.add_argument("--no-stop-on-target", dest="stop_on_target", action="store_false", help="No detenerse al alcanzar el setpoint (usar horizonte fijo).")
    parser.set_defaults(stop_on_target=True)
    parser.add_argument("--max-horizonte-min", type=int, default=1440, help="Tope maximo en minutos cuando se usa stop-on-target (default 1440 = 24h).")
    parser.add_argument("--sin-grafico", action="store_true", help="No mostrar grafico.")
    args = parser.parse_args()    

    df = cargar_global()
    df_3comp, df_4comp = separar_por_arquitectura(df)

    sensor_nums = detectar_sensores_disponibles(df)

    if not sensor_nums:
        print("No se encontraron columnas de sensor ox_sN en el dataset global.")
        return

    eventos_por_sensor = []
    for sensor in sensor_nums:
        eventos_3comp = calcular_tiempo_hasta_setpoint(
            df_3comp,
            sensor=sensor,
            horizonte_min=args.horizonte_min,
            stop_on_target=args.stop_on_target,
            max_horizonte_min=args.max_horizonte_min,
        )
        eventos_4comp = calcular_tiempo_hasta_setpoint(
            df_4comp,
            sensor=sensor,
            horizonte_min=args.horizonte_min,
            stop_on_target=args.stop_on_target,
            max_horizonte_min=args.max_horizonte_min,
        )

        if not eventos_3comp.empty:
            eventos_por_sensor.append(eventos_3comp.assign(grupo="3comp"))
        if not eventos_4comp.empty:
            eventos_por_sensor.append(eventos_4comp.assign(grupo="4comp"))

    if not eventos_por_sensor:
        print("No se encontraron eventos validos en ningun sensor.")
        return

    eventos = pd.concat(eventos_por_sensor, ignore_index=True)
    out_csv = BASE_DIR / "eventos_global_all_sensors.csv"
    eventos.to_csv(out_csv, index=False)
    mediana_3comp = eventos.loc[eventos["grupo"] == "3comp", "minutos_hasta_setpoint"].median()
    mediana_4comp = eventos.loc[eventos["grupo"] == "4comp", "minutos_hasta_setpoint"].median()
    mediana_global = eventos["minutos_hasta_setpoint"].median()

    print(f"Eventos validos 3comp (todos los sensores): {int((eventos['grupo'] == '3comp').sum()):,}")
    print(f"Mediana global minutos hasta setpoint 3comp: {mediana_3comp}")
    print(f"Eventos validos 4comp (todos los sensores): {int((eventos['grupo'] == '4comp').sum()):,}")
    print(f"Mediana global minutos hasta setpoint 4comp: {mediana_4comp}")
    print(f"Mediana global total (todos los sensores y arquitecturas): {mediana_global}")
    print(f"Eventos guardados en: {out_csv}")

    # no graficar automáticamente cuando se analizan múltiples sensores
    if not args.sin_grafico and len(sensor_nums) == 1 and not eventos.empty:
        graficar_evento(df_4comp, eventos[(eventos['sensor'] == sensor_nums[0]) & (eventos['grupo'] == '4comp')], sensor=sensor_nums[0], indice_evento=0)


if __name__ == "__main__":
    main()