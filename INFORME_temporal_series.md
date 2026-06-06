**Informe: Serie temporal — PSA y tiempo hasta setpoint**

**Resumen Ejecutivo:**
- Objetivo: estimar cuántos minutos es necesario mantener encendidos los módulos PSA para que el oxígeno medido en jaulas alcance el setpoint configurado.
- Enfoque: detectar arranques de `suma_psa` (0 → >0), seguir el sensor `ox_sX` y medir minutos hasta el primer cruce sobre `sp_sX` dentro de un horizonte.

**Datos usados:**
- dataset_3comp_limpio.csv: 1,987,226 filas | 14 fuentes | rango temporal: 2026-02-01 00:00:04.607 → 2026-05-25 18:04:21.162
- dataset_4comp_limpio.csv: 5,549,700 filas | 41 fuentes | rango temporal: 2026-02-01 00:00:03.469 → 2026-05-25 16:45:22.672
- Frecuencia: ~1 minuto (datos casi regulares por minuto).

**Metodología implementada (script temporal_series.py):**
1. Carga del CSV y orden por `source`, `TIME`.
2. Detección de eventos: `psa_on = suma_psa > 0`; evento = transición `psa_on` de False → True.
3. Ventana de búsqueda: hasta `horizonte_min` minutos (por defecto 360 = 6 h) desde el inicio del evento.
4. Criterios de filtro para contar un evento válido:
   - `sp_sX` (setpoint) debe existir y ser > 0 (ignore setpoints 0 o negativos).
   - `ox_sX` en el momento de inicio debe existir y ser < setpoint (si ya está por encima, tiempo = 0 y se ignora para la métrica de recuperación).
   - Se busca el primer registro en la ventana donde `ox_sX >= target` (target = mediana de `sp_sX` en la ventana si `sp_sX` contiene nulos).
5. Resultado por evento: `minutos_hasta_setpoint` (NaN si no alcanza en el horizonte).
6. Opcional: gráfica del episodio real (ox, setpoint y estado PSA) para validar visualmente.

**Resultados:**
- Eventos válidos 3comp: 859
- Eventos válidos 4comp: 3019

- Mediana minutos hasta setpoint 3comp:
  - 8.0001

- Mediana minutos hasta setpoint 4comp:
  - 15.000174999999999

**Resultados (análisis global y por arquitectura, ejecución más reciente sobre global_concatenado.CSV):**
- Archivo analizado: global_concatenado.CSV (consolidado de fuentes)
- Filas procesadas: 8,644,192
- Fuentes: 60
- Eventos válidos 3comp: 1,480
- Mediana minutos hasta setpoint 3comp:
  - 9.000266666666667
- Eventos válidos 4comp: 3,030
- Mediana minutos hasta setpoint 4comp:
  - 15.0002
- Eventos guardados en: eventos_global_sensor1.csv (ruta: C:\\Users\\Guill\\Mineria De Datos\\proyecto_mineria\\eventos_global_sensor1.csv)

**Interpretación rápida:**
- La mediana sugiere que los pontones del conjunto 3comp requieren ~8 min medianos para recuperar hasta el setpoint cuando parten por debajo, y los de 4comp ~15 min.
- Un número importante de eventos no alcanza el setpoint dentro del horizonte (esto requiere investigar causas: setpoint muy alto, fallas, tiempo insuficiente o ruido en medición).

**Cómo reproducir (ejecutar script):**

En la carpeta del proyecto ejecutar:

```bash
python temporal_series.py --sensor 1
```

Opciones útiles:
- `--sensor N` : analiza el sensor N (1..12).
- `--horizonte-min K` : ventana máxima en minutos, si no se especifica el codigo dejara de observar una ves los niveles de oxigeno cumpla con el setpoint establecido.
- `--sin-grafico` : no mostrar la gráfica de ejemplo.

**Archivos relevantes en el repo:**
- temporal_series.py  (script que implementa la lógica descrita)
- dataset_3comp_limpio.csv
- dataset_4comp_limpio.csv
 - analisis_global_series.py (ejecución sobre global_concatenado.CSV; guarda eventos en eventos_global_sensor{n}.csv)
 - global_concatenado.CSV
 - eventos_global_sensor1.csv
