"""Audita viajes y genera un informe de dispatch a partir de tres CSV."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3

from graficos import formato, graficar, q90
from simular import CAMPOS


RAIZ = Path(__file__).resolve().parents[1]
CORTE_EJEMPLO = "2025-09-22 18:00:00"
TIEMPOS = {"salida", "llegada", "inicio_descarga", "fin_descarga", "registrado_en", "disponible_en"}


def fecha(valor):
    d = datetime.fromisoformat(valor.strip())
    if d.tzinfo is not None or d.microsecond or len(valor.strip()) <= 10:
        raise ValueError("Usa fecha y hora local de Lima con precisión de segundos.")
    return d.isoformat(sep=" ", timespec="seconds")


def convertir(campo, valor):
    valor = valor.strip()
    if not valor:
        raise ValueError(f"{campo}: se requiere un valor.")
    if campo in TIEMPOS:
        return fecha(valor)
    if campo in {"carga_tmh", "peso_tmh", "cu_pct"}:
        n = float(valor)
        if not math.isfinite(n):
            raise ValueError(f"{campo}: usa un número finito.")
        return n
    if campo == "aprobado":
        return int(valor)
    return valor


def cargar(conn, carpeta):
    conn.executescript((RAIZ / "sql/esquema.sql").read_text(encoding="utf-8"))
    for tabla, campos in CAMPOS.items():
        ruta = carpeta / f"{tabla}.csv"
        with ruta.open(encoding="utf-8-sig", newline="") as archivo:
            lector = csv.DictReader(archivo)
            nombres = lector.fieldnames or []
            if len(nombres) != len(set(nombres)) or not set(campos).issubset(nombres):
                raise ValueError(f"{ruta.name}: comprueba los encabezados. Se requieren {', '.join(campos)}.")
            sql = f"INSERT INTO {tabla} ({','.join(campos)}) VALUES ({','.join('?' for _ in campos)})"
            for fila in lector:
                l = lector.line_num
                try:
                    if None in fila or any(fila[c] is None for c in campos):
                        raise ValueError("La fila tiene una cantidad de campos incorrecta.")
                    conn.execute(sql, [convertir(c, fila[c]) for c in campos])
                except (ValueError, sqlite3.IntegrityError) as error:
                    raise ValueError(f"{ruta.name}, línea {l}: {error}") from error


def exportar(ruta, consulta):
    campos = [c[0] for c in consulta.description]
    with ruta.open("w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.writer(f)
        escritor.writerow(campos)
        for fila in consulta:
            escritor.writerow([round(v, 6) if isinstance(v, float) else v for v in fila])


def resumir(viajes, zonas, orfanas, corte, alto, bajo, tolerancia_peso):
    qa = Counter(f["estado_qaqc"] for f in viajes)
    evaluables = [f for f in viajes if f["estado_qaqc"] == "validado" and f["cu_referencia_pct"] is not None]
    alertas = Counter(f["alerta_ruta"] for f in evaluables)
    tmh = defaultdict(float)
    for f in evaluables:
        tmh[f["alerta_ruta"]] += f["peso_tmh"]
    cambiados = sum(f["cambio_destino"] == 1 for f in viajes)
    por_hora = defaultdict(list)
    for f in viajes:
        if f["estado_qaqc"] == "validado" and f["destino_recibido"] == "CHANCADO":
            por_hora[f["llegada"][11:13]].append(f["espera_min"])
    horas = {h: {"viajes": len(x), "p90_min": round(q90(x), 3)} for h, x in por_hora.items() if len(x) >= 10}
    mayor_espera = max(horas, key=lambda h: horas[h]["p90_min"]) if horas else None
    masa = sum(tmh.values())
    incidencia = masa - tmh["coincide"]
    return {
        "corte": corte, "ley_chancado_pct": alto, "ley_acopio_pct": bajo, "tolerancia_peso_pct": tolerancia_peso,
        "viajes_cargados": len(viajes), "qaqc": dict(qa), "viajes_evaluables_ruta": len(evaluables),
        "viajes_validados_sin_ley_referencia": qa["validado"] - len(evaluables),
        "viajes_con_reenvio_identico": sum(f["firmas_recepcion"] == 1 and f["eventos_recepcion"] > 1 for f in viajes),
        "eventos_huerfanos": orfanas, "cambios_destino_vs_plan": cambiados,
        "casos_ruta": dict(alertas), "tmh_por_caso_ruta": {k: round(v, 2) for k,v in tmh.items()},
        "tmh_evaluables": round(masa, 2), "tmh_con_discrepancia": round(incidencia, 2),
        "pct_tmh_discrepancia": round(100 * incidencia / masa, 4) if masa else None,
        "discrepancias_por_momento_de_ley": dict(Counter(f["visibilidad_alerta"] for f in evaluables
                                                        if f["visibilidad_alerta"])),
        "mayor_tasa_zona_turno": dict(max(zonas,key=lambda z:z["pct_tmh_alerta"])) if zonas else None,
        "p90_chancado_por_hora_llegada": horas, "hora_con_mayor_p90_chancado": mayor_espera,
    }


def informe(r, salida):
    qa, t = r["qaqc"], r["tmh_por_caso_ruta"]
    n = r["viajes_cargados"]
    v = qa.get("validado", 0)
    mayor = r["mayor_tasa_zona_turno"]
    h = r["hora_con_mayor_p90_chancado"]
    turno = (
        f"{mayor['zona']} · {mayor['turno']} tuvo la mayor tasa de discrepancia "
        f"({formato(mayor['pct_tmh_alerta'], 1)} % de {formato(mayor['tmh_evaluables'])} TMH evaluables)."
        if mayor else "La corrida carece de viajes evaluables para comparar zonas."
    )
    cola = (
        f"A las {h}:00, el P90 de espera en chancado fue "
        f"**{formato(r['p90_chancado_por_hora_llegada'][h]['p90_min'], 1)} min** "
        f"con {r['p90_chancado_por_hora_llegada'][h]['viajes']} recepciones validadas. "
        "Revisar en conjunto el volumen de llegadas y la bitácora de chancado de esa franja."
        if h else "La muestra de recepciones por hora no alcanza el mínimo de 10 para comparar el P90."
    )
    cobertura = f"{formato(100*v/n,1)} %" if n else "sin viajes"
    pct_ruta = "sin denominador" if r["pct_tmh_discrepancia"] is None else f"{formato(r['pct_tmh_discrepancia'],1)} %"
    contenido = f"""# Informe de dispatch y control de mineral

Corte del análisis {r['corte']} · Hora local de Lima
Caso de portafolio con datos sintéticos. TMH son toneladas métricas húmedas.

| Indicador | Resultado |
| --- | ---: |
| Viajes cargados | {formato(n)} |
| Recepciones validadas | {formato(v)} ({cobertura}) |
| Viajes evaluables con ley de referencia | {formato(r['viajes_evaluables_ruta'])} |
| Cambios de destino frente al plan | {formato(r['cambios_destino_vs_plan'])} |
| TMH evaluables para control de destino | {formato(r['tmh_evaluables'])} |
| TMH con discrepancia frente a la regla de referencia | {formato(r['tmh_con_discrepancia'])} ({pct_ruta}) |
| TMH de material de chancado recibidas en botadero | {formato(t.get('mineral_a_botadero',0))} |
| TMH de material bajo corte recibidas en chancado | {formato(t.get('bajo_corte_a_chancado',0))} |

## Qué investigar primero

1. {turno} Priorizar los viajes a botadero y comprobar trazabilidad, instrucción de turno y versión de ley disponible.
2. Se identificaron {r['discrepancias_por_momento_de_ley'].get('surge_con_referencia_posterior',0)} discrepancias que aparecen con una referencia posterior. Analizar esos casos junto con Geología antes de atribuir una desviación a Dispatch.
3. {cola}

## Calidad del dato

Hay **{formato(n-v)} viajes** por resolver al corte. Incluyen recepciones faltantes, firmas contradictorias, secuencias inválidas y pesos en revisión. Se consolidaron {r['viajes_con_reenvio_identico']} viajes con reenvío idéntico y se informaron {r['eventos_huerfanos']} eventos sin viaje padre.

La regla usa {formato(r['ley_chancado_pct'],2)} % Cu o más para chancado y entre {formato(r['ley_acopio_pct'],2)} % Cu y el corte de chancado para acopio. El modelo de bloque al corte es una referencia simulada para priorizar casos. Las diferencias requieren contraste con geología y operación antes de estimar pérdidas, dilución o impacto económico.
"""
    salida.write_text(contenido, encoding="utf-8")


def ejecutar(datos, salida, corte, alto, bajo, peso):
    if not 0 <= bajo < alto <= 100 or not 0 <= peso <= 100:
        raise ValueError("Revisa los cortes de ley y la tolerancia de peso. Ingresa porcentajes.")
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        cargar(conn, datos)
        conn.execute("CREATE TEMP TABLE criterios(corte, corte_alta, corte_acopio, peso_tolerancia_pct)")
        conn.execute("INSERT INTO criterios VALUES (?, ?, ?, ?)", (corte, alto, bajo, peso))
        conn.executescript((RAIZ / "sql/analisis.sql").read_text(encoding="utf-8"))
        viajes = conn.execute("SELECT * FROM viajes_auditados ORDER BY salida, viaje_id").fetchall()
        if not viajes:
            raise ValueError("No hay viajes hasta la fecha de corte indicada.")
        kpis = conn.execute("SELECT * FROM kpi_turno ORDER BY dia_operativo, turno").fetchall()
        zonas = conn.execute("SELECT * FROM rutas_zona ORDER BY pct_tmh_alerta DESC").fetchall()
        orfanas = conn.execute("SELECT COUNT(*) FROM qa_orfanas").fetchone()[0]
        r = resumir(viajes, zonas, orfanas, corte, alto, bajo, peso)
        salida.mkdir(parents=True, exist_ok=True)
        for nombre, consulta in {
            "viajes_auditados": "SELECT * FROM viajes_auditados ORDER BY salida, viaje_id",
            "kpi_turno": "SELECT * FROM kpi_turno ORDER BY dia_operativo, turno",
            "rutas_zona": "SELECT * FROM rutas_zona ORDER BY pct_tmh_alerta DESC",
            "revision_destinos": """SELECT * FROM viajes_auditados
                    WHERE alerta_ruta NOT IN ('coincide','sin_validar','sin_ley_referencia')
                    ORDER BY CASE alerta_ruta WHEN 'mineral_a_botadero' THEN 0 ELSE 1 END, peso_tmh DESC""",
            "qa_orfanas": "SELECT * FROM qa_orfanas ORDER BY registrado_en",
        }.items():
            exportar(salida / f"{nombre}.csv", conn.execute(consulta))
        figuras = salida / "figuras"
        figuras.mkdir(exist_ok=True)
        graficar(viajes, kpis, zonas, figuras)
        informe(r, salida / "informe.md")
        (salida / "resumen.json").write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--datos", type=Path, help="Carpeta con viajes.csv, recepciones.csv y leyes.csv.")
    p.add_argument("--salida", type=Path)
    p.add_argument("--corte", type=fecha, help='Hora local, por ejemplo "2025-09-22 18:00:00".')
    p.add_argument("--ley-chancado", type=float, default=.30)
    p.add_argument("--ley-acopio", type=float, default=.18)
    p.add_argument("--tolerancia-peso", type=float, default=5)
    args = p.parse_args()
    if args.datos and not (args.corte and args.salida):
        p.error("Para datos propios también indica --corte y --salida.")
    try:
        r = ejecutar(args.datos or RAIZ/"datos", args.salida or RAIZ/"resultados",
                     args.corte or CORTE_EJEMPLO, args.ley_chancado, args.ley_acopio, args.tolerancia_peso)
    except (OSError, ValueError, sqlite3.Error) as error:
        p.exit(2,f"Error: {error}\n")
    print(f"{r['viajes_cargados']} viajes · {r['viajes_evaluables_ruta']} evaluables · "
          f"{formato(r['tmh_con_discrepancia'])} TMH con discrepancia.")


if __name__ == "__main__":
    main()
