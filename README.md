# proyecto_mineria


# Orden preguntas 

### Fase 1: Contexto Operativo y Físico (Entendiendo la realidad)

*El objetivo de esta fase es comprender cómo funciona la planta en el mundo real antes de hablar de tablas de datos.*

* **Funcionamiento General:** ¿Podrían explicarnos con un poco más de detalle el flujo de funcionamiento físico de la planta (Generador eléctrico -> Compresores -> PSA -> Jaulas)?
* **Ciclos y Control:** ¿El ciclo de encendido y apagado de los compresores siempre es el mismo, o varía? Además, ¿encender un compresor es una acción binaria (on/off) o tiene niveles de potencia modulables (ej. uso de control PID)?
* **Contexto del Setpoint:** ¿El setpoint de oxígeno (`sp_s`) varía según la especie de pez, el tamaño de la jaula o la estación del año, o es una constante fija por pontón? o el una persona la setea y manipula a gusto?
* **Factores Externos (Causalidad):** Más allá de la demanda de las jaulas, ¿existen factores externos que no estén en este dataset (como marejadas, temperatura del agua, o cambios de clima) que causen picos de demanda de oxígeno?
* **Impacto del Problema:** ¿Cuál es el impacto económico o biológico real cuando el nivel de oxígeno cae por debajo del setpoint en una jaula?

### Fase 2: Validación de Supuestos de Limpieza (Mostrando su trabajo)

*Aquí es donde demuestran que ya metieron las manos en los datos y validan sus decisiones arquitectónicas.*

* **Las Arquitecturas "Irreales":** Encontramos 5 pontones (ej. POX1, POX47) que reportaban 3 compresores pero solo 4 PSAs. Como la regla general parece ser "3 Compresores van con 6 PSA", decidimos eliminar estos centros asumiendo que eran experimentales o defectuosos. ¿Existen pontones operativos con esa configuración 3-4, o hicimos bien en filtrarlos?
* **La "Basura" del PLC:** Asumimos que los pontones que mostraban señales de latido (`hb_com4`) para el compresor 4, pero tenían su presión y marcha en nulo, eran un error de configuración del PLC (plantilla copiada). ¿Es correcto asumir que esa señal es ruido y debe eliminarse en sistemas de 3 compresores?
* **Mantenimientos de 8/24 Horas:** Identificamos apagones totales de telemetría que duraban exactamente 480 o 1440 minutos. Asumimos que eran ventanas de mantenimiento preventivo y creamos la variable `horas_desde_mantencion`. ¿Estas caídas exactas corresponden efectivamente a mantenimientos programados? ¿El tiempo transcurrido desde ese evento afecta el rendimiento del oxígeno?
* **Micro-cortes en Sala de Máquinas:** Vimos que la sala de máquinas a veces se desconecta por menos de 15 minutos mientras las jaulas siguen reportando oxígeno. Aplicamos "relleno hacia adelante" asumiendo que las máquinas siguieron operando igual. ¿Es seguro asumir que la presión de los PSA se mantiene relativamente estable durante un micro-corte tan breve?

### Fase 3: Diccionario de Datos y Variables Específicas

*Aclarar las columnas que aún son un misterio.*

* **El prefijo "m_":** ¿Qué significa exactamente el prefijo `m_` en los sensores de las jaulas (`m_s1` a `m_s12`)? ¿Indica "marcha", "modo", u otra métrica?
* **El Heartbeat ("hb_"):** ¿Qué evento físico o de red provoca específicamente que no se genere un *heartbeat* en un sensor que sí está conectado?

### Fase 4: Viabilidad del Modelo y Variable Objetivo (Data Mining)

*Crucial para asegurar que su proyecto cumpla con los requisitos del curso.*

* **Registro de Fallas Reales:** Para poder entrenar nuestro modelo, ¿existe algún registro histórico de fallas reales (alarmas del sistema o bitácoras de mantenciones correctivas)? Si no, tendremos que inferir las fallas estrictamente desde los datos.
* **Métrica Continua de Degradación:** Dado que queremos predecir la falla antes de que ocurra (y no solo clasificar si "falla o no falla"), ¿qué métrica continua observan ustedes para medir la degradación del sistema? Por ejemplo, ¿podemos usar la pérdida gradual de presión (psi) como indicador de un colapso inminente?
* **Frecuencia y Estacionalidad:** ¿Con qué frecuencia real ocurren estos eventos críticos de asimetría o caída del sistema? Además, ¿existe alguna época del año en particular donde ocurran más fallas?
* **Reinicio del Sistema:** ¿Qué constituye físicamente un "reinicio no deseado"? En los datos de telemetría, ¿hay forma de distinguir un reinicio forzado por falla de un reinicio programado?

---

Esta estructura les permitirá dominar la reunión, demostrando que entienden tanto la logística del proceso como los requerimientos técnicos del análisis de datos.

Sabiendo que el avance del proyecto consiste en una presentación en clases, si OXZO les confirmara que los pontones con 3 compresores y 4 PSAs sí son configuraciones reales y operativas, ¿cómo adaptarían su pipeline de limpieza para reintegrar esos datos antes de la entrega?