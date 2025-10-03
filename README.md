TP2 – Sistemas Operativos (Simulador de Asignación de Memoria)

Descripción
-----------
Este proyecto implementa un simulador gráfico en Python para ilustrar
diferentes esquemas de administración de memoria y planificación de procesos
en Sistemas Operativos.

Incluye:
- Memoria contigua con estrategias de asignación: Primer ajuste, Mejor ajuste
  y Peor ajuste.
- Función de compactación manual en memoria contigua.
- Memoria paginada, con opción de reemplazo FIFO.
- Planificación Round Robin, con quantum configurable.
- Simulación de recurso compartido con exclusión mutua (impresora).
- Visualización gráfica de uso de memoria y métricas por tick.
- Exportación de resultados a CSV y gráfico en formato PNG.
- Interfaz en modo oscuro y en idioma español.

Requisitos
----------
- Python 3.10 o superior
- Bibliotecas:
  - tkinter (incluida en la mayoría de distribuciones de Python)
  - matplotlib

Instalación de dependencias:
    pip install matplotlib

Ejecución
---------
Para iniciar el simulador, ejecutar en consola:

    python tp2_gui_oscuro_es.py

Uso de la interfaz
------------------
Parámetros configurables:
- Modo: CONTIGUA o PAGINACION
- Asignador (solo para CONTIGUA): Primer, Mejor o Peor ajuste
- Memoria total: tamaño de la memoria en unidades
- Tamaño de página (solo para PAGINACION): tamaño en unidades
- Quantum: valor de quantum para Round Robin
- Ticks: cantidad de pasos a simular
- Procesos (stress): número de procesos a generar
- Semilla: para reproducibilidad de los procesos generados
- Reemplazo FIFO (solo en PAGINACION): activa política de reemplazo
- Velocidad: milisegundos entre cada tick de simulación

Controles principales:
- Iniciar: comienza la simulación con los parámetros establecidos.
- Pausar/Reanudar: detiene o continúa la simulación.
- Reiniciar: limpia el entorno para una nueva ejecución.
- Compactar ahora: ejecuta compactación en memoria CONTIGUA.
- Escenarios preconfigurados: botones para ejecutar ejemplos comunes.
- Exportar CSV: genera el archivo "reporte.CSV" con métricas por tick.
- Guardar gráfico: exporta el gráfico de uso de memoria en PNG.

Visualización:
- Listado de procesos en ejecución, listos y bloqueados.
- Barra de memoria, con colores por proceso y huecos libres diferenciados.
- Gráfico de evolución de uso de memoria/frames, con marcadores en
  ticks donde se aplicó compactación.

Resultados
----------
Al finalizar la simulación, se muestra un resumen con:
- Cantidad de procesos completados
- Tiempo de espera promedio
- Tiempo de retorno promedio
- Uso promedio de memoria
- Fragmentación promedio (en CONTIGUA)
- Fallos de página (en PAGINACION)

Notas
-----
- La compactación solo aplica en modo CONTIGUA.
- El botón de compactación se deshabilita automáticamente en otros modos.
- El simulador está diseñado con fines didácticos para ilustrar la
  administración de memoria en Sistemas Operativos.
