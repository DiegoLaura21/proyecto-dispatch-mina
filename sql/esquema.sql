CREATE TABLE viajes (
    viaje_id TEXT PRIMARY KEY,
    salida TEXT NOT NULL,
    camion TEXT NOT NULL,
    pala TEXT NOT NULL,
    zona TEXT NOT NULL,
    bloque TEXT NOT NULL,
    destino_plan TEXT NOT NULL CHECK (destino_plan IN ('CHANCADO','ACOPIO','BOTADERO')),
    carga_tmh REAL NOT NULL CHECK (carga_tmh > 0)
);

-- La recepción puede llegar sin viaje padre. Se conserva para auditar su origen.
CREATE TABLE recepciones (
    evento_id TEXT PRIMARY KEY,
    viaje_id TEXT NOT NULL,
    destino TEXT NOT NULL CHECK (destino IN ('CHANCADO','ACOPIO','BOTADERO')),
    llegada TEXT NOT NULL,
    inicio_descarga TEXT NOT NULL,
    fin_descarga TEXT NOT NULL,
    registrado_en TEXT NOT NULL,
    peso_tmh REAL NOT NULL
);

CREATE TABLE leyes (
    bloque TEXT NOT NULL,
    disponible_en TEXT NOT NULL,
    cu_pct REAL NOT NULL CHECK (cu_pct BETWEEN 0 AND 100),
    aprobado INTEGER NOT NULL CHECK (aprobado IN (0,1)),
    PRIMARY KEY (bloque, disponible_en)
);

CREATE INDEX idx_recepcion_viaje ON recepciones(viaje_id, registrado_en);
CREATE INDEX idx_viajes_salida ON viajes(salida);
