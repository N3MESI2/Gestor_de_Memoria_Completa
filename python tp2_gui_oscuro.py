#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TP2 – Sistemas Operativos (Modo Oscuro) – Interfaz en Español (COMPLETA)

Incluye:
- CONTIGUA: Primer/Mejor/Peor ajuste + compactación manual
- PAGINACIÓN: con/sin reemplazo FIFO
- Planificación Round Robin (Quantum configurable)
- Recurso compartido: Impresora (exclusión mutua simulada con semáforo binario)
- Gráfico de uso + marcadores de compactación
- Exportar reporte.CSV y guardar PNG
- Barra de memoria estética (bloques por proceso/huecos, colores y tooltip)
- Switch de vista: Detallada (bloques) / Compacta (porcentaje)
- NUEVO: Tooltips (ayuda emergente) sobre cada botón y cada casilla
"""

import enum
import random
import math
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import csv
import os
import colorsys
import hashlib

import tkinter as tk
from tkinter import ttk, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ===========================
# Modelo de proceso y estados
# ===========================

class Estado(enum.Enum):
    NUEVO = "Nuevo"
    LISTO = "Listo"
    EJECUTANDO = "Ejecutando"
    BLOQUEADO = "Bloqueado"
    TERMINADO = "Terminado"


@dataclass
class Proceso:
    pid: int
    llegada: int
    duracion: int
    demanda_mem: int
    cpu_restante: int = field(init=False)
    estado: Estado = field(default=Estado.NUEVO)
    espera_acumulada: int = 0
    tiempo_fin: Optional[int] = None
    # CONTIGUA
    bloque_asignado: Optional[Tuple[int, int]] = None
    # PAGINACIÓN
    paginas: List[int] = field(default_factory=list)
    marcos: List[Optional[int]] = field(default_factory=list)
    # I/O (impresora)
    proximo_io_en: Optional[int] = None

    def __post_init__(self):
        self.cpu_restante = self.duracion


# ===========================
# Memoria contigua
# ===========================

@dataclass
class Hueco:
    inicio: int
    tamanio: int


class Asignador(enum.Enum):
    PRIMER_AJUSTE = "primer"
    MEJOR_AJUSTE = "mejor"
    PEOR_AJUSTE = "peor"


class MemoriaContigua:
    def __init__(self, total: int, asignador: Asignador):
        self.total = total
        self.asignador = asignador
        self.huecos: List[Hueco] = [Hueco(0, total)]
        self.asignaciones: dict[int, Tuple[int, int]] = {}
        self.hist_uso: List[float] = []
        self.hist_frag: List[float] = []

    def _buscar_hueco(self, tamanio: int) -> Optional[int]:
        candidatos = [(i, h.inicio, h.tamanio) for i, h in enumerate(self.huecos) if h.tamanio >= tamanio]
        if not candidatos:
            return None

        if self.asignador == Asignador.PRIMER_AJUSTE:
            return candidatos[0][0]

        if self.asignador == Asignador.MEJOR_AJUSTE:
            # Best-fit con desempate estable por menor dirección
            i, _, _ = min(candidatos, key=lambda x: (x[2], x[1]))
            return i

        # Peor-ajuste con desempate estable por menor dirección
        i, _, _ = max(candidatos, key=lambda x: (x[2], -x[1]))
        return i

    def asignar(self, pid: int, tamanio: int) -> Optional[Tuple[int, int]]:
        idx = self._buscar_hueco(tamanio)
        if idx is None:
            return None
        hueco = self.huecos[idx]
        inicio = hueco.inicio
        self.asignaciones[pid] = (inicio, tamanio)
        hueco.inicio += tamanio
        hueco.tamanio -= tamanio
        if hueco.tamanio == 0:
            self.huecos.pop(idx)
        return (inicio, tamanio)

    def liberar(self, pid: int):
        if pid not in self.asignaciones:
            return
        inicio, tamanio = self.asignaciones.pop(pid)
        self.huecos.append(Hueco(inicio, tamanio))
        self.huecos.sort(key=lambda h: h.inicio)
        fusionados: List[Hueco] = []
        for h in self.huecos:
            if not fusionados:
                fusionados.append(h)
            else:
                ultimo = fusionados[-1]
                if ultimo.inicio + ultimo.tamanio == h.inicio:
                    ultimo.tamanio += h.tamanio
                else:
                    fusionados.append(h)
        self.huecos = fusionados

    def compactar(self):
        # Desplaza todo al inicio para eliminar fragmentación externa
        asign_orden = sorted([(pid, i, t) for pid, (i, t) in self.asignaciones.items()],
                             key=lambda x: x[1])
        actual = 0
        nuevas = {}
        for pid, _, tam in asign_orden:
            nuevas[pid] = (actual, tam)
            actual += tam
        self.asignaciones = nuevas
        self.huecos = [Hueco(actual, self.total - actual)]

    def proporcion_uso(self) -> float:
        usado = sum(t for _, t in self.asignaciones.values())
        return usado / self.total if self.total else 0.0

    def grado_fragmentacion(self) -> float:
        libre = sum(h.tamanio for h in self.huecos)
        if libre == 0:
            return 0.0
        mayor = max((h.tamanio for h in self.huecos), default=0)
        return 1.0 - (mayor / libre)

    def tick_metricas(self):
        self.hist_uso.append(self.proporcion_uso())
        self.hist_frag.append(self.grado_fragmentacion())


# ===========================
# Memoria por paginación
# ===========================

class MemoriaPaginacion:
    def __init__(self, total: int, tamanio_pagina: int, reemplazo_fifo: bool = False):
        self.total = total
        self.tamanio_pagina = tamanio_pagina
        self.cant_marcos = max(1, total // tamanio_pagina)
        self.marcos: List[Optional[Tuple[int, int]]] = [None] * self.cant_marcos
        self.marcos_libres: set[int] = set(range(self.cant_marcos))
        self.usar_fifo = reemplazo_fifo
        self.cola_fifo: deque[int] = deque()
        self.hist_uso: List[float] = []
        self.fallos_pagina: int = 0

    def asignar(self, proc: Proceso) -> bool:
        necesarias = len(proc.paginas)
        for idx_pag in range(necesarias):
            if proc.marcos[idx_pag] is not None:
                continue
            if self.marcos_libres:
                marco = self.marcos_libres.pop()
            elif self.usar_fifo:
                victima = self.cola_fifo.popleft()
                self.marcos[victima] = None
                marco = victima
            else:
                return False
            self.marcos[marco] = (proc.pid, idx_pag)
            proc.marcos[idx_pag] = marco
            if self.usar_fifo:
                self.cola_fifo.append(marco)
            self.fallos_pagina += 1
        return True

    def liberar(self, proc: Proceso):
        for m in proc.marcos:
            if m is not None and 0 <= m < self.cant_marcos:
                self.marcos[m] = None
                self.marcos_libres.add(m)
        proc.marcos = [None for _ in proc.paginas]

    def proporcion_uso(self) -> float:
        usados = sum(1 for x in self.marcos if x is not None)
        return usados / self.cant_marcos if self.cant_marcos else 0.0

    def tick_metricas(self):
        self.hist_uso.append(self.proporcion_uso())


# ===========================
# Recurso compartido (impresora)
# ===========================

class Impresora:
    def __init__(self):
        self.sem = 1
        self.pid_actual: Optional[int] = None

    def intentar_tomar(self, pid: int) -> bool:
        if self.sem > 0:
            self.sem -= 1
            self.pid_actual = pid
            return True
        return False

    def liberar(self):
        self.sem += 1
        self.pid_actual = None


# ===========================
# Planificador Round Robin
# ===========================

class PlanificadorRR:
    def __init__(self, quantum: int = 3):
        self.quantum = quantum
        self.quantum_actual = 0
        self.listos: deque[Proceso] = deque()
        self.ejecutando: Optional[Proceso] = None
        self.bloqueados: deque[Proceso] = deque()

    def admitir(self, p: Proceso):
        if p.estado == Estado.NUEVO:
            p.estado = Estado.LISTO
        self.listos.append(p)

    def bloquear(self, p: Proceso):
        p.estado = Estado.BLOQUEADO
        self.bloqueados.append(p)
        self.ejecutando = None
        self.quantum_actual = 0

    def desbloquear(self, p: Proceso):
        p.estado = Estado.LISTO
        self.listos.append(p)

    def desalojar_si_corresponde(self):
        if self.ejecutando and self.quantum_actual >= self.quantum:
            self.ejecutando.estado = Estado.LISTO
            self.listos.append(self.ejecutando)
            self.ejecutando = None
            self.quantum_actual = 0

    def planificar(self):
        if not self.ejecutando and self.listos:
            self.ejecutando = self.listos.popleft()
            self.ejecutando.estado = Estado.EJECUTANDO
            self.quantum_actual = 0

    def tick(self):
        for p in self.listos:
            p.espera_acumulada += 1
        if self.ejecutando:
            self.quantum_actual += 1


# ===========================
# Simulador principal
# ===========================

class Simulador:
    def __init__(self, memoria_total: int, modo: str, asignador: str = "primer", tamanio_pagina: int = 64,
                 quantum: int = 3, ticks: int = 200, stress: int = 15, semilla: int = 42, reemplazo_fifo: bool = False):
        random.seed(semilla)
        self.ticks = ticks
        self.ahora = 0
        self.modo = modo.upper()
        self.planificador = PlanificadorRR(quantum=quantum)
        self.impresora = Impresora()
        self.procesos: List[Proceso] = []
        self.llegadas: defaultdict[int, List[Proceso]] = defaultdict(list)
        self.finalizados: List[Proceso] = []
        self.mem_contigua: Optional[MemoriaContigua] = None
        self.mem_paginada: Optional[MemoriaPaginacion] = None

        self.hist_uso_pct: List[float] = []
        self.hist_frag: List[float] = []
        self.hist_fallos: List[int] = []

        if self.modo == "CONTIGUA":
            mapa = {"primer": Asignador.PRIMER_AJUSTE, "mejor": Asignador.MEJOR_AJUSTE, "peor": Asignador.PEOR_AJUSTE}
            self.mem_contigua = MemoriaContigua(memoria_total, mapa.get(asignador, Asignador.PRIMER_AJUSTE))
        elif self.modo == "PAGINACION":
            self.mem_paginada = MemoriaPaginacion(memoria_total, tamanio_pagina, reemplazo_fifo=reemplazo_fifo)
        else:
            raise ValueError("El modo debe ser CONTIGUA o PAGINACION")

        self._generar_procesos(stress=stress, memoria_total=memoria_total, tamanio_pagina=tamanio_pagina)

    def _generar_procesos(self, stress: int, memoria_total: int, tamanio_pagina: int):
        for pid in range(1, stress + 1):
            llegada = random.randint(0, max(1, self.ticks // 4))
            duracion = random.randint(5, 20)
            demanda = random.randint(int(0.05 * memoria_total), int(0.30 * memoria_total))
            p = Proceso(pid=pid, llegada=llegada, duracion=duracion, demanda_mem=demanda)
            p.proximo_io_en = random.choice([None, duracion // 2, duracion // 3])
            if self.modo == "PAGINACION":
                paginas = max(1, math.ceil(p.demanda_mem / tamanio_pagina))
                p.paginas = list(range(paginas))
                p.marcos = [None] * paginas
            self.procesos.append(p)
            self.llegadas[llegada].append(p)

    def _intentar_admitir_memoria(self, p: Proceso) -> bool:
        if self.modo == "CONTIGUA":
            asign = self.mem_contigua.asignar(p.pid, p.demanda_mem)
            if asign is not None:
                p.bloque_asignado = asign
                return True
            # Sin compactación automática: si falla, queda pendiente
            return False
        else:
            return self.mem_paginada.asignar(p)

    def _liberar_memoria(self, p: Proceso):
        if self.modo == "CONTIGUA":
            if p.bloque_asignado is not None:
                self.mem_contigua.liberar(p.pid)
                p.bloque_asignado = None
        else:
            self.mem_paginada.liberar(p)

    def paso(self):
        # 1) Llegadas
        for p in self.llegadas.get(self.ahora, []):
            if self._intentar_admitir_memoria(p):
                self.planificador.admitir(p)

        # 2) Reintentos de NUEVO
        for p in [x for x in self.procesos if x.estado == Estado.NUEVO and x.llegada <= self.ahora]:
            if self._intentar_admitir_memoria(p):
                self.planificador.admitir(p)

        # 3) Desbloqueos por I/O
        a_desbloquear = []
        for p in list(self.planificador.bloqueados):
            if p.proximo_io_en is not None and p.proximo_io_en < 0:
                p.proximo_io_en += 1
                if p.proximo_io_en == 0:
                    a_desbloquear.append(p)
        for p in a_desbloquear:
            self.planificador.bloqueados.remove(p)
            self.planificador.desbloquear(p)
            self.impresora.liberar()

        # 4) Planificación
        self.planificador.desalojar_si_corresponde()
        self.planificador.planificar()

        # 5) Ejecutar 1 tick
        if self.planificador.ejecutando:
            p = self.planificador.ejecutando
            if p.proximo_io_en is not None and p.cpu_restante == p.proximo_io_en:
                if self.impresora.intentar_tomar(p.pid):
                    p.proximo_io_en = -2  # 2 ticks de I/O simulada
                    self.planificador.bloquear(p)
            else:
                p.cpu_restante -= 1
                if p.cpu_restante <= 0:
                    p.estado = Estado.TERMINADO
                    p.tiempo_fin = self.ahora
                    self._liberar_memoria(p)
                    self.finalizados.append(p)
                    self.planificador.ejecutando = None
                    self.planificador.quantum_actual = 0

        # 6) Métricas por tick
        if self.modo == "CONTIGUA":
            self.mem_contigua.tick_metricas()
            self.hist_uso_pct.append(self.mem_contigua.hist_uso[-1])
            self.hist_frag.append(self.mem_contigua.hist_frag[-1])
        else:
            self.mem_paginada.tick_metricas()
            self.hist_uso_pct.append(self.mem_paginada.hist_uso[-1])
            self.hist_fallos.append(self.mem_paginada.fallos_pagina)

        self.planificador.tick()
        self.ahora += 1

    def uso_actual(self) -> float:
        if self.modo == "CONTIGUA":
            return self.mem_contigua.proporcion_uso()
        else:
            return self.mem_paginada.proporcion_uso()

    def extra_actual(self):
        if self.modo == "CONTIGUA":
            return ("Fragmentación", self.mem_contigua.grado_fragmentacion())
        else:
            return ("Fallos de página", self.mem_paginada.fallos_pagina)

    def terminado(self) -> bool:
        return len(self.finalizados) == len(self.procesos)

    def resultados(self):
        n = len(self.finalizados)
        if n == 0:
            return {"completados": 0, "total": len(self.procesos)}
        espera_prom = sum(p.espera_acumulada for p in self.finalizados) / n
        retorno_prom = sum((p.tiempo_fin - p.llegada) for p in self.finalizados) / n
        res = {"completados": n, "total": len(self.procesos),
               "espera_prom": espera_prom, "retorno_prom": retorno_prom}
        if self.modo == "CONTIGUA":
            uso_prom = sum(self.mem_contigua.hist_uso) / len(self.mem_contigua.hist_uso)
            frag_prom = sum(self.mem_contigua.hist_frag) / len(self.mem_contigua.hist_frag)
            libre_final = sum(h.tamanio for h in self.mem_contigua.huecos)
            mayor_hueco = max((h.tamanio for h in self.mem_contigua.huecos), default=0)
            res.update({"uso_prom": uso_prom, "frag_prom": frag_prom,
                        "libre_final": libre_final, "mayor_hueco": mayor_hueco})
        else:
            uso_prom = sum(self.mem_paginada.hist_uso) / len(self.mem_paginada.hist_uso)
            res.update({"uso_prom": uso_prom, "fallos_pagina": self.mem_paginada.fallos_pagina})
        return res


# ===========================
# Tooltip helper (para botones y casillas)
# ===========================

class Tooltip:
    def __init__(self, widget, text, delay=400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._tip = None
        self.widget.bind("<Enter>", self._schedule)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _e):
        self._unschedule()
        self._id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None

    def _show(self):
        if self._tip or not self.text:
            return
        x, y, cx, cy = self.widget.bbox("insert") if self.widget.winfo_viewable() else (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 20
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        frm = tk.Frame(tw, bg="#1f242e", bd=0)
        frm.pack()
        lbl = tk.Label(frm, text=self.text, justify="left",
                       bg="#1f242e", fg="#e6edf3",
                       relief="flat", bd=0, padx=8, pady=5,
                       font=("Consolas", 9))
        lbl.pack()
        # borde sutil
        tk.Frame(tw, bg="#30363d", height=1).place(x=0, y=0, relwidth=1)
        tk.Frame(tw, bg="#30363d", height=1).place(x=0, rely=1.0, relwidth=1, anchor="sw")
        tk.Frame(tw, bg="#30363d", width=1).place(y=0, x=0, relheight=1)
        tk.Frame(tw, bg="#30363d", width=1).place(rely=1.0, relheight=1, relx=1.0, anchor="se")

    def _hide(self, _e=None):
        self._unschedule()
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ===========================
# Interfaz gráfica (Tkinter)
# ===========================

class Aplicacion(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TP2 – Sistemas Operativos (Modo Oscuro)")
        self.configure(bg="#0f1115")

        self.sim: Optional[Simulador] = None
        self.ejecutando = False
        self.pausado = False
        self.ms_por_tick = 120

        # Cache de colores por PID
        self._color_cache = {}

        # Vista: detallada (bloques) o compacta (porcentaje)
        self.var_vista_detallada = tk.BooleanVar(value=True)

        self._construir_controles()
        self._construir_vistas()

    # ---- Controles ----
    def _construir_controles(self):
        fg = "#e6edf3"
        bg_panel = "#161b22"

        marco = tk.Frame(self, bg=bg_panel, bd=1, relief="solid")
        marco.pack(fill="x", padx=10, pady=10)

        fila1 = tk.Frame(marco, bg=bg_panel)
        fila1.pack(fill="x", padx=8, pady=6)

        lbl_modo = tk.Label(fila1, text="Modo", fg=fg, bg=bg_panel)
        lbl_modo.grid(row=0, column=0, sticky="w")
        self.var_modo = tk.StringVar(value="CONTIGUA")
        ttk.Style().configure("TCombobox", fieldbackground="#0f1115", background="#0f1115", foreground="white")
        self.cmb_modo = ttk.Combobox(fila1, textvariable=self.var_modo, values=["CONTIGUA", "PAGINACION"],
                                     width=12, state="readonly")
        self.cmb_modo.grid(row=0, column=1, padx=5)
        Tooltip(self.cmb_modo, "Selecciona el esquema de memoria: CONTIGUA o PAGINACIÓN")

        lbl_asig = tk.Label(fila1, text="Asignador (CONTIGUA)", fg=fg, bg=bg_panel)
        lbl_asig.grid(row=0, column=2, sticky="w")

        self.opciones_asign = {
            "Primer ajuste": "primer",
            "Mejor ajuste": "mejor",
            "Peor ajuste": "peor",
        }
        self.var_asign = tk.StringVar(value="Primer ajuste")
        self.cmb_asign = ttk.Combobox(
            fila1,
            textvariable=self.var_asign,
            values=list(self.opciones_asign.keys()),
            width=18,
            state="readonly",
            justify="left"
        )
        self.cmb_asign.grid(row=0, column=3, padx=5)
        Tooltip(self.cmb_asign, "Estrategia de ubicación para memoria CONTIGUA")

        tk.Label(fila1, text="Memoria total", fg=fg, bg=bg_panel).grid(row=0, column=4, sticky="w")
        self.ent_mem = tk.Entry(fila1, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_mem.insert(0, "1024")
        self.ent_mem.grid(row=0, column=5, padx=5)
        Tooltip(self.ent_mem, "Tamaño total de memoria (unidades)")

        tk.Label(fila1, text="Tamaño de página (PAGINACIÓN)", fg=fg, bg=bg_panel).grid(row=0, column=6, sticky="w")
        self.ent_pag = tk.Entry(fila1, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_pag.insert(0, "64")
        self.ent_pag.grid(row=0, column=7, padx=5)
        Tooltip(self.ent_pag, "Tamaño de página/frames (solo en PAGINACIÓN)")

        tk.Label(fila1, text="Quantum", fg=fg, bg=bg_panel).grid(row=0, column=8, sticky="w")
        self.ent_q = tk.Entry(fila1, width=6, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_q.insert(0, "3")
        self.ent_q.grid(row=0, column=9, padx=5)
        Tooltip(self.ent_q, "Quantum del Round Robin (ticks por turno)")

        fila2 = tk.Frame(marco, bg=bg_panel)
        fila2.pack(fill="x", padx=8, pady=6)

        tk.Label(fila2, text="Ticks", fg=fg, bg=bg_panel).grid(row=0, column=0, sticky="w")
        self.ent_ticks = tk.Entry(fila2, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_ticks.insert(0, "150")
        self.ent_ticks.grid(row=0, column=1, padx=5)
        Tooltip(self.ent_ticks, "Duración total de la simulación en ticks")

        tk.Label(fila2, text="Procesos (stress)", fg=fg, bg=bg_panel).grid(row=0, column=2, sticky="w")
        self.ent_stress = tk.Entry(fila2, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_stress.insert(0, "18")
        self.ent_stress.grid(row=0, column=3, padx=5)
        Tooltip(self.ent_stress, "Cantidad de procesos a generar")

        tk.Label(fila2, text="Semilla", fg=fg, bg=bg_panel).grid(row=0, column=4, sticky="w")
        self.ent_semilla = tk.Entry(fila2, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_semilla.insert(0, "42")
        self.ent_semilla.grid(row=0, column=5, padx=5)
        Tooltip(self.ent_semilla, "Semilla para reproducibilidad de los procesos")

        self.var_fifo = tk.BooleanVar(value=False)
        self.chk_fifo = tk.Checkbutton(fila2, text="Reemplazo FIFO (PAGINACIÓN)", variable=self.var_fifo,
                                       fg=fg, bg=bg_panel, selectcolor=bg_panel,
                                       activebackground=bg_panel, activeforeground=fg)
        self.chk_fifo.grid(row=0, column=6, padx=10)
        Tooltip(self.chk_fifo, "Si se llena, reemplaza frames con política FIFO")

        tk.Label(fila2, text="Velocidad (ms/tick)", fg=fg, bg=bg_panel).grid(row=0, column=7, sticky="w")
        self.ent_vel = tk.Entry(fila2, width=10, bg="#0d1117", fg=fg, insertbackground=fg)
        self.ent_vel.insert(0, "120")
        self.ent_vel.grid(row=0, column=8, padx=5)
        Tooltip(self.ent_vel, "Tiempo real entre ticks (en milisegundos)")

        # --- Switch de vista detallada/compacta ---
        self.chk_vista = tk.Checkbutton(
            fila2,
            text="Vista detallada de memoria",
            variable=self.var_vista_detallada,
            fg=fg, bg=bg_panel, selectcolor=bg_panel,
            activebackground=bg_panel, activeforeground=fg,
            command=self._dibujar_memoria
        )
        self.chk_vista.grid(row=0, column=9, padx=10, sticky="w")
        Tooltip(self.chk_vista, "Alterna entre bloques por proceso y barra de porcentaje")

        fila3 = tk.Frame(marco, bg=bg_panel)
        fila3.pack(fill="x", padx=8, pady=6)
        self.btn_iniciar = tk.Button(fila3, text="Iniciar", command=self.iniciar, bg="#238636", fg="white")
        self.btn_iniciar.pack(side="left", padx=4)
        Tooltip(self.btn_iniciar, "Crea procesos y empieza/relanza la simulación")

        self.btn_pausa = tk.Button(fila3, text="Pausar", command=self.pausar_reanudar, bg="#d29922", fg="black")
        self.btn_pausa.pack(side="left", padx=4)
        Tooltip(self.btn_pausa, "Pausa o reanuda el avance de ticks")

        self.btn_reiniciar = tk.Button(fila3, text="Reiniciar", command=self.reiniciar, bg="#f85149", fg="white")
        self.btn_reiniciar.pack(side="left", padx=4)
        Tooltip(self.btn_reiniciar, "Detiene y limpia todo para empezar de cero")

        self.btn_compactar = tk.Button(fila3, text="Compactar ahora", command=self.compactar_ahora,
                                       bg="#21262d", fg="#e6edf3")
        self.btn_compactar.pack(side="left", padx=4)
        Tooltip(self.btn_compactar, "Memoria CONTIGUA: desplaza bloques para eliminar fragmentación")

        fila4 = tk.Frame(marco, bg=bg_panel)
        fila4.pack(fill="x", padx=8, pady=6)
        tk.Label(fila4, text="Escenarios:", fg=fg, bg=bg_panel, font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(0, 10)
        )
        b1 = tk.Button(fila4, text="CONTIGUA + Primer/Ajuste", bg="#21262d", fg=fg,
                       command=self.preset_contigua_primer)
        b1.pack(side="left", padx=3)
        Tooltip(b1, "Configura CONTIGUA + Primer ajuste y ejecuta")

        b2 = tk.Button(fila4, text="CONTIGUA + Mejor/Ajuste", bg="#21262d", fg=fg,
                       command=self.preset_contigua_mejor)
        b2.pack(side="left", padx=3)
        Tooltip(b2, "Configura CONTIGUA + Mejor ajuste y ejecuta")

        b3 = tk.Button(fila4, text="PAGINACIÓN (sin reemplazo)", bg="#21262d", fg=fg,
                       command=self.preset_paginacion)
        b3.pack(side="left", padx=3)
        Tooltip(b3, "Configura PAGINACIÓN sin reemplazo y ejecuta")

        b4 = tk.Button(fila4, text="PAGINACIÓN + FIFO", bg="#21262d", fg=fg,
                       command=self.preset_paginacion_fifo)
        b4.pack(side="left", padx=3)
        Tooltip(b4, "Configura PAGINACIÓN con reemplazo FIFO y ejecuta")

        fila5 = tk.Frame(marco, bg=bg_panel)
        fila5.pack(fill="x", padx=8, pady=6)
        tk.Label(fila5, text="Exportar:", fg=fg, bg=bg_panel, font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(0, 10)
        )
        bcsv = tk.Button(fila5, text="Exportar CSV", bg="#21262d", fg=fg, command=self.exportar_csv)
        bcsv.pack(side="left", padx=3)
        Tooltip(bcsv, "Guarda métricas por tick en reporte.CSV")

        bpng = tk.Button(fila5, text="Guardar gráfico (PNG)", bg="#21262d", fg=fg, command=self.guardar_png)
        bpng.pack(side="left", padx=3)
        Tooltip(bpng, "Exporta el gráfico de uso en una imagen PNG")

    # ---- Vistas ----
    def _construir_vistas(self):
        fg = "#e6edf3"
        subfg = "#9da7b3"
        bg_panel = "#161b22"

        encabezado = tk.Frame(self, bg=bg_panel, bd=1, relief="solid")
        encabezado.pack(fill="x", padx=10, pady=(0, 10))
        self.lbl_titulo = tk.Label(encabezado, text="Modo: -  |  t=0", fg=fg, bg=bg_panel, font=("Segoe UI", 12, "bold"))
        self.lbl_titulo.pack(side="left", padx=8, pady=6)

        principal = tk.Frame(self, bg=self["bg"])
        principal.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        izq = tk.Frame(principal, bg=bg_panel, bd=1, relief="solid")
        izq.pack(side="left", fill="both", expand=True, padx=(0, 5))
        tk.Label(izq, text="EJECUTANDO", fg="#31d07d", bg=bg_panel, font=("Consolas", 12, "bold")).pack(
            anchor="w", padx=8, pady=(8, 0)
        )
        self.lbl_run = tk.Label(izq, text="-", fg=fg, bg=bg_panel, font=("Consolas", 11))
        self.lbl_run.pack(anchor="w", padx=12, pady=4)
        tk.Label(izq, text="LISTOS", fg=fg, bg=bg_panel, font=("Consolas", 12, "bold")).pack(
            anchor="w", padx=8, pady=(8, 0)
        )
        self.lbl_ready = tk.Label(izq, text="[]", fg=subfg, bg=bg_panel, font=("Consolas", 10))
        self.lbl_ready.pack(anchor="w", padx=12, pady=2)
        tk.Label(izq, text="BLOQUEADOS", fg="#d29922", bg=bg_panel, font=("Consolas", 12, "bold")).pack(
            anchor="w", padx=8, pady=(8, 0)
        )
        self.lbl_block = tk.Label(izq, text="[]", fg=subfg, bg=bg_panel, font=("Consolas", 10))
        self.lbl_block.pack(anchor="w", padx=12, pady=2)

        der = tk.Frame(principal, bg=bg_panel, bd=1, relief="solid")
        der.pack(side="left", fill="both", expand=True, padx=(5, 0))
        tk.Label(der, text="Memoria", fg=fg, bg=bg_panel, font=("Segoe UI", 12, "bold")).pack(
            anchor="w", padx=8, pady=(8, 0)
        )
        self.lienzo = tk.Canvas(der, height=40, bg="#0d1117", highlightthickness=0)
        self.lienzo.pack(fill="x", padx=8, pady=(6, 2))
        self.lbl_uso = tk.Label(der, text="Uso: 0%", fg=fg, bg=bg_panel, font=("Consolas", 10))
        self.lbl_uso.pack(anchor="w", padx=8, pady=(0, 4))
        self.lbl_extra = tk.Label(der, text="", fg=subfg, bg=bg_panel, font=("Consolas", 10))
        self.lbl_extra.pack(anchor="w", padx=8, pady=(0, 8))

        # Mini leyenda
        self.leyenda = tk.Frame(der, bg=bg_panel)
        self.leyenda.pack(fill="x", padx=8, pady=(0, 8))

        self.fig = Figure(figsize=(5, 2.3), dpi=100, facecolor="#0f1115")
        self.ax = self.fig.add_subplot(111, facecolor="#0f1115")
        self.ax.tick_params(colors="#9da7b3")
        for sp in self.ax.spines.values():
            sp.set_color("#30363d")
        self.ax.set_title("Uso de memoria/frames (%)", color="#e6edf3", fontsize=10)
        self.ax.set_xlabel("tick", color="#9da7b3")
        self.ax.set_ylabel("%", color="#9da7b3")
        self.xdatos, self.ydatos = [], []
        (self.linea,) = self.ax.plot([], [], linewidth=2)
        self.canvas_plot = FigureCanvasTkAgg(self.fig, master=der)
        self.canvas_plot.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Marcadores de compactación
        self.compact_ticks = []      # ticks donde hubo compactación
        self._compact_lines = []     # artistas de axvline
        self._legend_added = False   # evitar múltiples leyendas

        # Tooltip de bloques/huecos (barra de memoria)
        self.tooltip = tk.Label(self, text="", bg="#1f242e", fg="#e6edf3",
                                font=("Consolas", 9), bd=0, padx=6, pady=3)
        self.tooltip.place_forget()
        self._hover_bind()

    # ------ Estética / utilidades ------
    def _hover_bind(self):
        self.lienzo.bind("<Motion>", self._on_canvas_motion)
        self.lienzo.bind("<Leave>", lambda e: self.tooltip.place_forget())

    def _on_canvas_motion(self, event):
        # Si la vista es compacta: no hay tooltip de bloques
        if not getattr(self, "var_vista_detallada", None) or not self.var_vista_detallada.get():
            self.tooltip.place_forget()
            return
        item = self.lienzo.find_closest(event.x, event.y)
        tags = self.lienzo.gettags(item)
        info = None
        for t in tags:
            if t.startswith("bloque:"):
                _, pid, label = t.split(":", 2)
                info = label.replace("|", "\n")
                break
            if t.startswith("hueco:"):
                _, label = t.split(":", 1)
                info = label.replace("|", "\n")
                break
        if info:
            self.tooltip.config(text=info)
            x = self.winfo_pointerx() - self.winfo_rootx() + 12
            y = self.winfo_pointery() - self.winfo_rooty() + 12
            self.tooltip.place(x=x, y=y)
        else:
            self.tooltip.place_forget()

    def _color_pid(self, pid: int) -> str:
        if pid in self._color_cache:
            return self._color_cache[pid]
        h = (int(hashlib.md5(str(pid).encode()).hexdigest(), 16) % 360) / 360.0
        s = 0.55
        l = 0.52
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        hexcol = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        self._color_cache[pid] = hexcol
        return hexcol

    def _chip_leyenda(self, pid: int):
        marco = tk.Frame(self.leyenda, bg="#161b22")
        col = self._color_pid(pid)
        dot = tk.Canvas(marco, width=14, height=14, bg="#161b22", highlightthickness=0)
        dot.create_oval(2, 2, 12, 12, fill=col, outline=col)
        dot.pack(side="left", padx=(0, 6))
        tk.Label(marco, text=f"PID {pid}", bg="#161b22", fg="#b9c3cf", font=("Consolas", 9)).pack(side="left")
        return marco

    # ---- Lógica ----
    def _leer_parametros(self):
        try:
            modo = self.var_modo.get().upper()
            asign = self.opciones_asign[self.var_asign.get()]  # visible -> interno
            mem = int(self.ent_mem.get())
            pag = int(self.ent_pag.get())
            q = int(self.ent_q.get())
            ticks = int(self.ent_ticks.get())
            stress = int(self.ent_stress.get())
            semilla = int(self.ent_semilla.get())
            fifo = bool(self.var_fifo.get())
            vel = int(self.ent_vel.get())
        except ValueError:
            messagebox.showerror("Error", "Revisá que los parámetros numéricos sean válidos.")
            return None
        return dict(modo=modo, asignador=asign, memoria_total=mem, tamanio_pagina=pag, quantum=q,
                    ticks=ticks, stress=stress, semilla=semilla, reemplazo_fifo=fifo, velocidad=vel)

    def iniciar(self):
        if self.ejecutando:
            messagebox.showinfo("Simulación", "Ya hay una simulación en curso. Reiniciá para empezar otra.")
            return
        p = self._leer_parametros()
        if not p:
            return
        self.ms_por_tick = p.pop("velocidad")
        self.sim = Simulador(**p)
        self._reiniciar_grafico()
        self.ejecutando = True
        self.pausado = False
        self._bucle()

    def _bucle(self):
        if not self.ejecutando:
            return
        if not self.pausado and self.sim and not self.sim.terminado():
            self.sim.paso()
            self._dibujar_memoria()
            self._actualizar_listas()
            self._actualizar_grafico()
            self.after(self.ms_por_tick, self._bucle)
        else:
            if self.sim and self.sim.terminado():
                self._dibujar_memoria()
                self._actualizar_listas()
                self._actualizar_grafico()
                self._mostrar_resultados()
                self.ejecutando = False
            else:
                self.after(self.ms_por_tick, self._bucle)

    def pausar_reanudar(self):
        if not self.ejecutando:
            return
        self.pausado = not self.pausado
        self.btn_pausa.config(text="Reanudar" if self.pausado else "Pausar")

    def reiniciar(self):
        self.ejecutando = False
        self.pausado = False
        self.sim = None
        self._resetear_vistas()

    # Presets
    def preset_contigua_primer(self):
        self.var_modo.set("CONTIGUA")
        self.var_asign.set("Primer ajuste")
        self.ent_mem.delete(0, tk.END); self.ent_mem.insert(0, "1024")
        self.ent_ticks.delete(0, tk.END); self.ent_ticks.insert(0, "150")
        self.ent_stress.delete(0, tk.END); self.ent_stress.insert(0, "18")
        self.var_fifo.set(False)
        self.iniciar()

    def preset_contigua_mejor(self):
        self.var_modo.set("CONTIGUA")
        self.var_asign.set("Mejor ajuste")
        self.ent_mem.delete(0, tk.END); self.ent_mem.insert(0, "1024")
        self.ent_ticks.delete(0, tk.END); self.ent_ticks.insert(0, "150")
        self.ent_stress.delete(0, tk.END); self.ent_stress.insert(0, "18")
        self.var_fifo.set(False)
        self.iniciar()

    def preset_paginacion(self):
        self.var_modo.set("PAGINACION")
        self.ent_mem.delete(0, tk.END); self.ent_mem.insert(0, "1024")
        self.ent_pag.delete(0, tk.END); self.ent_pag.insert(0, "64")
        self.ent_ticks.delete(0, tk.END); self.ent_ticks.insert(0, "150")
        self.ent_stress.delete(0, tk.END); self.ent_stress.insert(0, "18")
        self.var_fifo.set(False)
        self.iniciar()

    def preset_paginacion_fifo(self):
        self.var_modo.set("PAGINACION")
        self.ent_mem.delete(0, tk.END); self.ent_mem.insert(0, "1024")
        self.ent_pag.delete(0, tk.END); self.ent_pag.insert(0, "64")
        self.ent_ticks.delete(0, tk.END); self.ent_ticks.insert(0, "150")
        self.ent_stress.delete(0, tk.END); self.ent_stress.insert(0, "18")
        self.var_fifo.set(True)
        self.iniciar()

    # Exportación
    def exportar_csv(self):
        if not self.sim:
            messagebox.showwarning("reporte.CSV", "Primero iniciá una simulación.")
            return
        nombre = "reporte.CSV"
        ruta = os.path.join(os.getcwd(), nombre)
        with open(ruta, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if self.sim.modo == "CONTIGUA":
                w.writerow(["tick", "uso_porcentaje", "fragmentacion"])
                for t, (u, fr) in enumerate(zip(self.sim.hist_uso_pct, self.sim.hist_frag)):
                    w.writerow([t, round(u * 100, 3), round(fr, 5)])
            else:
                w.writerow(["tick", "uso_porcentaje", "fallos_pagina"])
                for t, (u, fp) in enumerate(zip(self.sim.hist_uso_pct, self.sim.hist_fallos)):
                    w.writerow([t, round(u * 100, 3), fp])
        messagebox.showinfo("reporte.CSV", f"Archivo guardado en:\n{ruta}")

    def guardar_png(self):
        ts = time.strftime("%Y%m%d_%H%M%S")
        nombre = f"tp2_grafico_{ts}.png"
        ruta = os.path.join(os.getcwd(), nombre)
        self.fig.savefig(ruta, dpi=150, bbox_inches="tight")
        messagebox.showinfo("Guardar PNG", f"Imagen guardada en:\n{ruta}")

    # ---------- Dibujo de memoria ----------
    def _dibujar_memoria(self):
        if not self.sim:
            return
        self.lienzo.delete("all")
        w = self.lienzo.winfo_width() or 600
        h = self.lienzo.winfo_height() or 40
        padding = 4
        x0, y0 = padding, padding
        x1, y1 = w - padding, h - padding

        # Fondo + borde sutil
        self.lienzo.create_rectangle(x0, y0, x1, y1, fill="#0d1117", outline="#22272e", width=1)

        # Métricas básicas
        uso = self.sim.uso_actual() * 100
        self.lbl_uso.config(text=f"Uso: {uso:.1f}%")
        etiqueta, valor = self.sim.extra_actual()
        if self.sim.modo == "CONTIGUA":
            self.lbl_extra.config(text=f"{etiqueta}: {valor:.2f}")
            total = self.sim.mem_contigua.total
        else:
            self.lbl_extra.config(text=f"{etiqueta}: {valor}")
            total = self.sim.mem_paginada.total
        self.lbl_titulo.config(text=f"Modo: {self.sim.modo}  |  t={self.sim.ahora}")

        # --- Vista compacta: barra % ---
        if not self.var_vista_detallada.get():
            usados_px = int((uso / 100.0) * (x1 - x0))
            self.lienzo.create_rectangle(x0, y0, x0 + usados_px, y1, fill="#238636", outline="", width=0)
            # Limpiar leyenda y tooltip
            for wdg in list(self.leyenda.children.values()):
                wdg.destroy()
            self.tooltip.place_forget()
            return

        # --- Vista detallada: bloques por proceso/huecos ---
        if self.sim.modo == "CONTIGUA":
            blocks = []
            free_blocks = []
            asigns = sorted([(pid, ini, tam) for pid, (ini, tam) in self.sim.mem_contigua.asignaciones.items()],
                            key=lambda x: x[1])
            cursor = 0
            for pid, ini, tam in asigns:
                if ini > cursor:
                    free_blocks.append((cursor, ini - cursor))
                blocks.append((pid, ini, tam))
                cursor = ini + tam
            if cursor < total:
                free_blocks.append((cursor, total - cursor))

            # Procesos
            for pid, ini, tam in blocks:
                if tam <= 0:
                    continue
                col = self._color_pid(pid)
                fx = x0 + (ini / total) * (x1 - x0)
                fw = max(1, (tam / total) * (x1 - x0))
                fy0 = y0 + 2
                fy1 = y1 - 2
                self.lienzo.create_rectangle(
                    fx, fy0, fx + fw, fy1, fill=col, outline="#0b0e13", width=1,
                    tags=(f"bloque:{pid}:PID {pid}|Inicio {ini}|Tamaño {tam}",)
                )
                if fw > 36:
                    self.lienzo.create_text(fx + fw/2, (fy0 + fy1)/2, text=f"P{pid}", fill="#0b0e13",
                                            font=("Consolas", 9, "bold"))

            # Huecos
            for ini, tam in free_blocks:
                if tam <= 0:
                    continue
                fx = x0 + (ini / total) * (x1 - x0)
                fw = max(1, (tam / total) * (x1 - x0))
                fy0 = y0 + 2
                fy1 = y1 - 2
                self.lienzo.create_rectangle(
                    fx, fy0, fx + fw, fy1, fill="#0f141b", outline="#29313a", dash=(2, 2),
                    tags=(f"hueco:Libre|Inicio {ini}|Tamaño {tam}",)
                )
        else:
            # PAGINACION: frames
            frames = self.sim.mem_paginada.cant_marcos
            ancho = (x1 - x0)
            alto0 = y0 + 2
            alto1 = y1 - 2
            frame_pid = []
            for par in self.sim.mem_paginada.marcos:
                if par is None:
                    frame_pid.append(None)
                else:
                    pid, _ = par
                    frame_pid.append(pid)

            for i, pid in enumerate(frame_pid):
                fx = x0 + (i / frames) * ancho
                fw = max(1, (1 / frames) * ancho)
                if pid is None:
                    self.lienzo.create_rectangle(
                        fx, alto0, fx + fw, alto1, fill="#0f141b", outline="#29313a",
                        tags=(f"hueco:Frame {i}|Libre",)
                    )
                else:
                    col = self._color_pid(pid)
                    self.lienzo.create_rectangle(
                        fx, alto0, fx + fw, alto1, fill=col, outline="#0b0e13",
                        tags=(f"bloque:{pid}:PID {pid}|Frame {i}",)
                    )

        # Leyenda (máx 6 PIDs visibles)
        for wdg in list(self.leyenda.children.values()):
            wdg.destroy()
        pids_visibles = []
        for item in self.lienzo.find_all():
            for t in self.lienzo.gettags(item):
                if t.startswith("bloque:"):
                    pid = int(t.split(":")[1])
                    if pid not in pids_visibles:
                        pids_visibles.append(pid)
        for pid in pids_visibles[:6]:
            chip = self._chip_leyenda(pid)
            chip.pack(side="left", padx=4)

    def _actualizar_listas(self):
        if not self.sim:
            return
        run_pid = self.sim.planificador.ejecutando.pid if self.sim.planificador.ejecutando else "-"
        self.lbl_run.config(text=str(run_pid))
        self.lbl_ready.config(text=str([p.pid for p in self.sim.planificador.listos]))
        self.lbl_block.config(text=str([p.pid for p in self.sim.planificador.bloqueados]))

    def _reiniciar_grafico(self):
        self.xdatos, self.ydatos = [], []
        self.linea.set_data([], [])
        self.ax.set_xlim(0, 30)
        self.ax.set_ylim(0, 100)

        # limpiar líneas de compactación previas
        for ln in getattr(self, "_compact_lines", []):
            try:
                ln.remove()
            except Exception:
                pass
        self._compact_lines = []
        self.compact_ticks = []
        self._legend_added = False

        self.canvas_plot.draw()

    def _actualizar_grafico(self):
        self.xdatos.append(self.sim.ahora)
        self.ydatos.append(self.sim.uso_actual() * 100.0)
        self.linea.set_data(self.xdatos, self.ydatos)
        if self.xdatos:
            self.ax.set_xlim(0, max(30, self.xdatos[-1]))
        self.ax.set_ylim(0, 100)

        # --- Redibujar marcadores de compactación ---
        for ln in self._compact_lines:
            try:
                ln.remove()
            except Exception:
                pass
        self._compact_lines = []
        for i, t in enumerate(self.compact_ticks):
            ln = self.ax.axvline(
                x=t,
                linestyle="--",
                linewidth=1.2,
                color="#f85149",
                label="Compactación" if i == 0 and not self._legend_added else None
            )
            self._compact_lines.append(ln)
        if self.compact_ticks and not self._legend_added:
            leg = self.ax.legend(loc="upper left", frameon=True)
            leg.get_frame().set_facecolor("#0f1115")
            leg.get_frame().set_edgecolor("#30363d")
            for txt in leg.get_texts():
                txt.set_color("#e6edf3")
            self._legend_added = True

        self.canvas_plot.draw()

    def _resetear_vistas(self):
        self.lbl_titulo.config(text="Modo: -  |  t=0")
        self.lbl_run.config(text="-")
        self.lbl_ready.config(text="[]")
        self.lbl_block.config(text="[]")
        self.lbl_uso.config(text="Uso: 0%")
        self.lbl_extra.config(text="")
        self.lienzo.delete("all")
        for wdg in list(self.leyenda.children.values()):
            wdg.destroy()
        self._reiniciar_grafico()

    def _mostrar_resultados(self):
        if not self.sim:
            return
        r = self.sim.resultados()
        if r.get("completados", 0) == 0:
            msg = f"Completados: {r.get('completados',0)}/{r.get('total','?')}\n" \
                  f"Ajustá parámetros o aumentá los ticks."
        else:
            base = f"Completados: {r['completados']}/{r.get('total','?')}\n"
            base += f"Espera promedio: {r['espera_prom']:.2f} ticks\n"
            base += f"Retorno (turnaround) promedio: {r['retorno_prom']:.2f} ticks\n"
            if 'uso_prom' in r:
                base += f"Uso promedio: {r['uso_prom'] * 100:.2f}%\n"
            if 'frag_prom' in r:
                base += f"Frag. promedio: {r['frag_prom']:.3f}\n" \
                        f"Libre final: {r['libre_final']}  |  Hueco mayor: {r['mayor_hueco']}\n"
            if 'fallos_pagina' in r:
                base += f"Fallos de página: {r['fallos_pagina']}\n"
            msg = base
        messagebox.showinfo("Resultados", msg)

    # Compactación manual
    def compactar_ahora(self):
        if not self.sim:
            messagebox.showwarning("Compactar", "Primero iniciá una simulación.")
            return
        if self.sim.modo != "CONTIGUA":
            messagebox.showinfo("Compactar", "La compactación aplica solo en memoria CONTIGUA.")
            return

        # Ejecutar compactación
        self.sim.mem_contigua.compactar()

        # Registrar tick de compactación (evitar duplicados consecutivos)
        if not self.compact_ticks or self.compact_ticks[-1] != self.sim.ahora:
            self.compact_ticks.append(self.sim.ahora)

        # Refrescar vistas
        self._dibujar_memoria()
        self._actualizar_listas()
        self._actualizar_grafico()
        messagebox.showinfo("Compactar", f"Memoria compactada en t={self.sim.ahora}.")


# ===========================
# Inicio
# ===========================

if __name__ == "__main__":
    app = Aplicacion()
    app.mainloop()
