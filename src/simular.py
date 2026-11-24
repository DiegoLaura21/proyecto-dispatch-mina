"""Genera una semana ficticia de dispatch de la mina Cerro Lucero."""

import argparse
import csv
from datetime import datetime, timedelta
from heapq import heapify, heappop, heappush
from pathlib import Path
from random import Random


RAIZ = Path(__file__).resolve().parents[1]
INICIO = datetime(2025, 9, 8, 7)
FIN = INICIO + timedelta(days=7)
DESTINOS = ("CHANCADO", "ACOPIO", "BOTADERO")
ZONAS = {
    "Contacto Este": [(.27, .36), (.33, .38), (.16, .15), (.24, .25), (.39, .41), (.14, .16)],
    "Banco Norte": [(.42, .43), (.40, .41), (.19, .23), (.14, .15), (.25, .27), (.17, .16)],
    "Banco Sur": [(.37, .35), (.32, .27), (.15, .16), (.18, .21), (.23, .26), (.12, .13)],
}
CAMPOS = {
    "viajes": ["viaje_id", "salida", "camion", "pala", "zona", "bloque", "destino_plan", "carga_tmh"],
    "recepciones": ["evento_id", "viaje_id", "destino", "llegada", "inicio_descarga",
                    "fin_descarga", "registrado_en", "peso_tmh"],
    "leyes": ["bloque", "disponible_en", "cu_pct", "aprobado"],
}


def hora(t):
    return t.isoformat(sep=" ", timespec="seconds")


def destino_ley(ley):
    return "CHANCADO" if ley >= .30 else "ACOPIO" if ley >= .18 else "BOTADERO"


def simular():
    rng = Random(65124)
    lotes = {}
    leyes = []
    for j, (zona, pares) in enumerate(ZONAS.items(), start=1):
        lotes[zona] = []
        for i, (preliminar, final) in enumerate(pares, start=1):
            bloque = f"Z{j}-B{i:02}"
            revision = INICIO + (timedelta(days=8, hours=4) if i % 2 else timedelta(days=3, hours=6))
            lotes[zona].append((bloque, preliminar, final, revision))
            leyes.append((bloque, hora(INICIO - timedelta(hours=20)), preliminar, 1))
            leyes.append((bloque, hora(revision), final, 1))
            # La carga del 9 de septiembre contiene una versión que QA no aprobó.
            if i % 3 == 0:
                leyes.append((bloque, hora(INICIO + timedelta(days=1, hours=12)), min(.65, preliminar + .16), 0))

    flota = [(INICIO + timedelta(minutes=rng.randrange(40)), f"CA-{camion:02}") for camion in range(1, 33)]
    heapify(flota)
    viajes, recepciones = [], []

    def recepcion(viaje, destino, llegada, inicio, fin, registro, masa):
        evento = f"R-{len(recepciones) + 1:06}"
        recepciones.append((evento, viaje, destino, hora(llegada), hora(inicio), hora(fin), hora(registro), masa))

    while flota[0][0] < FIN:
        salida, camion = heappop(flota)
        turno_noche = not 7 <= salida.hour < 19
        zona = rng.choices(list(ZONAS), [38, 32, 30], k=1)[0]
        pala = {"Contacto Este": "PA-01", "Banco Norte": "PA-02", "Banco Sur": "PA-03"}[zona]
        bloque, preliminar, final, informe = rng.choice(lotes[zona])
        plan = destino_ley(final if salida >= informe else preliminar)
        viaje = f"CL-{len(viajes) + 1:06}"
        carga = round(min(248, max(194, rng.gauss(226, 7))), 2)
        viajes.append((viaje, hora(salida), camion, pala, zona, bloque, plan, carga))

        prob_cambio = .16 if turno_noche and zona == "Contacto Este" else .04
        destino = plan
        if rng.random() < prob_cambio:
            destino = rng.choice([d for d in DESTINOS if d != plan])
        traslado = {"CHANCADO": 19, "ACOPIO": 15, "BOTADERO": 24}[destino] + rng.uniform(-5, 7)
        a = salida + timedelta(minutes=traslado)
        espera_base = 7 if destino == "CHANCADO" and turno_noche else 4 if destino == "CHANCADO" else 1.9
        # Ventana de congestión ficticia al inicio de la guardia de noche.
        if destino == "CHANCADO" and 19 <= salida.hour < 21:
            espera_base += 5
        espera = min(48, rng.gammavariate(2.8, espera_base / 2.8))
        descarga = a + timedelta(minutes=espera)
        fin = descarga + timedelta(minutes=rng.uniform(2.2, 4.5))
        registrado = fin + timedelta(minutes=rng.uniform(1, 17))
        peso = round(carga * rng.uniform(.981, 1.019), 2)
        siguiente = fin + timedelta(minutes=traslado * .75 + rng.uniform(6, 19))
        if rng.random() < .06:
            siguiente += timedelta(minutes=rng.uniform(16, 60))
        heappush(flota, (siguiente, camion))

        evento = rng.random()
        if evento < .018:
            continue
        if evento < .026:
            a = salida - timedelta(minutes=2)
        elif evento < .034:
            peso = round(peso * 1.18, 2)
        if len(viajes) % 173 == 0:
            registrado += timedelta(days=1)
        recepcion(viaje, destino, a, descarga, fin, registrado, peso)
        if .034 <= evento < .065:
            recepcion(viaje, destino, a, descarga, fin, registrado + timedelta(minutes=3), peso)
        elif .065 <= evento < .077:
            alterno = next(d for d in DESTINOS if d != destino)
            recepcion(viaje, alterno, a, descarga, fin, registrado + timedelta(minutes=9), peso)

    for i in range(3):
        a = INICIO + timedelta(days=1 + i, hours=2)
        recepcion(f"HUERFANO-{i+1}", "ACOPIO", a, a + timedelta(minutes=2),
                  a + timedelta(minutes=5), a + timedelta(minutes=7), 223.0)
    return {"viajes": viajes, "recepciones": recepciones, "leyes": leyes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, default=RAIZ / "datos")
    args = parser.parse_args()
    args.salida.mkdir(parents=True, exist_ok=True)
    for nombre, filas in simular().items():
        with (args.salida / f"{nombre}.csv").open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.writer(archivo)
            escritor.writerow(CAMPOS[nombre])
            escritor.writerows(filas)
        print(f"{nombre}: {len(filas)} registros")


if __name__ == "__main__":
    main()
