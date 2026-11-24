"""Gráficas para una revisión operativa de dispatch."""

from collections import defaultdict
from math import ceil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


TINTA = "#263b37"
DIA, NOCHE = "#348179", "#526d99"
ALERTA = "#b65d40"
DESTINOS = ["CHANCADO", "ACOPIO", "BOTADERO"]
ORDEN_TURNOS = {"Día": 0, "Noche": 1}


def formato(x, digitos=0):
    return f"{x:,.{digitos}f}".replace(",", " ").replace(".", ",")


def eje(ax, digitos=0):
    ax.set_major_formatter(FuncFormatter(lambda x, _: formato(x, digitos)))


def q90(valores):
    ordenados = sorted(valores)
    return ordenados[max(0, ceil(.9 * len(ordenados)) - 1)] if ordenados else float("nan")


def figura(titulo, nota, dos=True):
    fig, axes = plt.subplots(1, 2 if dos else 1, figsize=(12, 5.6), squeeze=False)
    fig.subplots_adjust(left=.09, right=.965, bottom=.17, top=.77, wspace=.32)
    fig.text(.076, .945, titulo, va="top", size=23, weight="bold")
    fig.text(.078, .87, nota, va="top", color="#516961")
    return fig, list(axes[0])


def guardar(fig, ruta):
    fig.savefig(ruta, dpi=150)
    plt.close(fig)


def cierre(kpis, ruta):
    kpis = sorted(kpis, key=lambda k: (k["dia_operativo"], ORDEN_TURNOS[k["turno"]]))
    fig, (ax, bx) = figura("Recepción y control de calidad por turno",
                           "Mina Cerro Lucero · La guardia de noche se asigna a su día de salida operativo")
    dias = sorted({k["dia_operativo"] for k in kpis})
    posiciones = {d: i for i, d in enumerate(dias)}
    grupos = [("Día", DIA, -.19), ("Noche", NOCHE, .19)]
    for turno, color, offset in grupos:
        filas = [k for k in kpis if k["turno"] == turno]
        x = [posiciones[k["dia_operativo"]] + offset for k in filas]
        ax.bar(x, [k["tmh_validadas"] for k in filas], .35, color=color, label=turno)
        bx.bar(x, [100*(k["sin_recepcion"]+k["otros_qaqc"])/k["viajes"] for k in filas],
               .35, color=color)
    for a in (ax, bx):
        a.set_xticks(range(len(dias)), [d[8:]+"/"+d[5:7] for d in dias],
                     rotation=42 if len(dias)>8 else 0)
        a.grid(axis="y", color="#e1ebe7")
        eje(a.yaxis)
    ax.set(ylabel="Toneladas húmedas", title="Recepción validada")
    bx.set(ylabel="% de viajes cargados", title="Viajes pendientes de revisión")
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=2,
               bbox_to_anchor=(.5,.035))
    guardar(fig, ruta)


def rutas(viajes, zonas, ruta):
    fig, (ax, bx) = figura("Discrepancias de destino por zona",
                           "Viajes con recepción validada y ley de referencia al corte · Datos sintéticos")
    pesos = defaultdict(float)
    for f in viajes:
        if f["estado_qaqc"] == "validado" and f["ruta_regla_referencia"]:
            pesos[f["ruta_regla_referencia"], f["destino_recibido"]] += f["peso_tmh"]/1000
    m = [[pesos[a,b] for b in DESTINOS] for a in DESTINOS]
    maxima = max(max(f) for f in m) or 1
    ax.imshow(m, cmap="YlGnBu", vmin=0, vmax=maxima)
    for i, fila in enumerate(m):
        for j, v in enumerate(fila):
            ax.text(j,i,formato(v,1), ha="center", va="center",
                    color="white" if v > maxima*.58 else TINTA, weight="bold" if i!=j else "normal")
    nombres = ["Chancado","Acopio","Botadero"]
    ax.set_xticks(range(3), nombres)
    ax.set_yticks(range(3), nombres)
    ax.set(xlabel="Destino registrado", ylabel="Regla de referencia", title="Matriz de destino (miles de TMH)")
    orden = sorted(zonas, key=lambda f: f["pct_tmh_alerta"])
    bx.barh(range(len(orden)), [f["pct_tmh_alerta"] for f in orden],
            color=[DIA if f["turno"]=="Día" else NOCHE for f in orden])
    bx.set_yticks(range(len(orden)), [f["zona"]+" · "+f["turno"] for f in orden], fontsize=9)
    bx.set(xlabel="% de las TMH evaluables", title="Tasa de discrepancia por zona y turno")
    bx.grid(axis="x", color="#e1ebe7")
    eje(bx.xaxis, 1)
    guardar(fig, ruta)


def espera(viajes, ruta):
    grupos = defaultdict(list)
    for f in viajes:
        if f["estado_qaqc"]=="validado" and f["destino_recibido"]=="CHANCADO":
            grupos[int(f["llegada"][11:13])].append(f["espera_min"])
    orden = list(range(7,24)) + list(range(7))
    fig, (ax,) = figura("Espera de recepción en chancado",
                        "P90 y volumen de recepciones por hora local de llegada · Datos sintéticos", dos=False)
    fig.subplots_adjust(right=.90)
    xs = list(range(24))
    ax.bar(xs,[q90(grupos[h]) if len(grupos[h])>=10 else float("nan") for h in orden],
           color=[DIA if 7<=h<19 else NOCHE for h in orden], width=.7)
    ax.axvline(11.5, color=ALERTA, alpha=.75, linestyle="--", label="19:00 · inicio de guardia")
    ax.set_xticks(xs,[f"{h:02d}" for h in orden])
    ax.set(ylabel="P90 de espera (min)", xlabel="Hora de llegada (hora de Lima)",
           title="P90 calculado con al menos 10 recepciones validadas por hora")
    ax.grid(axis="y",color="#e1ebe7")
    eje(ax.yaxis,1)
    bx = ax.twinx()
    bx.plot(xs,[len(grupos[h]) for h in orden],color=ALERTA, linewidth=1.7,label="Recepciones")
    bx.set_ylabel("Viajes recibidos", color=ALERTA)
    eje(bx.yaxis)
    ax.legend(loc="upper left")
    bx.legend(loc="upper right")
    guardar(fig,ruta)


def graficar(viajes, kpis, zonas, carpeta):
    plt.rcParams.update({
        "font.family":"DejaVu Sans","font.size":10,"figure.facecolor":"#f2f6f3",
        "axes.facecolor":"#ffffff","axes.edgecolor":"#cad7d0","axes.axisbelow":True,
        "axes.spines.top":False,"axes.spines.right":False,"text.color":TINTA,
        "axes.labelcolor":TINTA,"axes.titlecolor":TINTA,"legend.frameon":False,
    })
    cierre(kpis,carpeta/"01_recepcion.png")
    rutas(viajes,zonas,carpeta/"02_destinos.png")
    espera(viajes,carpeta/"03_espera_chancado.png")
