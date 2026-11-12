import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
import requests

from .domain import POI, Quote


ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def destinations():
    return json.loads((ROOT / "data/destinations.json").read_text())


def download_weather(destination, start="2015-01-01", end="2025-12-31"):
    params = dict(latitude=destination["latitude"], longitude=destination["longitude"],
                  start_date=start, end_date=end, models="era5", timezone=destination["timezone"],
                  daily="temperature_2m_max,precipitation_sum,wind_speed_10m_max", wind_speed_unit="kmh")
    response = requests.get("https://archive-api.open-meteo.com/v1/archive", params=params, timeout=180)
    response.raise_for_status()
    payload = response.json()
    daily = payload["daily"]
    frame = pd.DataFrame({"date": daily["time"], "temperature_c": daily["temperature_2m_max"],
                          "rain_mm": daily["precipitation_sum"], "wind_kmh": daily["wind_speed_10m_max"]})
    validate_weather(frame, start, end)
    city = destination["id"]
    write_json(ROOT / f"data/raw/weather/{city}.json", payload)
    target = ROOT / f"data/processed/weather/{city}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    write_json(target.with_suffix(".metadata.json"), {
        "source_url": response.url, "observed_at": datetime.now(timezone.utc).isoformat(),
        "valid_from": start, "valid_to": end, "data_kind": "reanalysis", "model": "era5",
        "version": "1", "confidence_note": "ERA5: реанализ, не измерение городской станции",
        "units": {"temperature_c": "°C", "rain_mm": "mm", "wind_kmh": "km/h"},
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "rows": len(frame)})
    return len(frame)


def validate_weather(frame, start=None, end=None):
    required = {"date", "temperature_c", "rain_mm", "wind_kmh"}
    if not required <= set(frame.columns) or frame.empty:
        raise ValueError("Неполная схема погоды")
    dates = pd.DatetimeIndex(pd.to_datetime(frame.date))
    expected = pd.date_range(start or dates.min(), end or dates.max())
    if not dates.equals(expected):
        raise ValueError("Даты должны идти ежедневно без пропусков и повторений")
    values = frame[["temperature_c", "rain_mm", "wind_kmh"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, 1:] < 0).any():
        raise ValueError("Пропущенные или недопустимые погодные значения")


def load_weather(city):
    frame = pd.read_csv(ROOT / f"data/processed/weather/{city}.csv", parse_dates=["date"])
    validate_weather(frame)
    return frame


def import_records(path: Path, model):
    raw = json.loads(Path(path).read_text())
    records = [model.model_validate(row) for row in raw]
    ids = [row.id for row in records]
    if len(ids) != len(set(ids)):
        raise ValueError("Повторяющиеся идентификаторы")
    known = {city["id"] for city in destinations()}
    if any(row.destination_id not in known for row in records):
        raise ValueError("Неизвестное направление")
    return records


def store_catalog(pois: List[POI], quotes: List[Quote], database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        for table, records in (("pois", pois), ("quotes", quotes)):
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, destination_id TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute(f"DELETE FROM {table}")
            connection.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)",
                                   [(r.id, r.destination_id, r.model_dump_json()) for r in records])


def import_osrm(payload, poi_ids, profile, graph_version):
    if profile != "foot" or not graph_version:
        raise ValueError("Нужны подтвержденные foot-профиль и версия графа")
    if payload.get("code") != "Ok":
        raise ValueError("OSRM не вернул корректную матрицу")
    n = len(poi_ids)
    if n != len(set(poi_ids)):
        raise ValueError("Повторные точки матрицы")
    for field in ("durations", "distances"):
        matrix = payload.get(field)
        if matrix is None or len(matrix) != n or any(len(row) != n for row in matrix):
            raise ValueError("Размерность матрицы не совпадает с точками")
        for row in matrix:
            if any(x is not None and (not np.isfinite(x) or x < 0) for x in row):
                raise ValueError("Некорректное значение матрицы")
    return dict(poi_ids=poi_ids, profile=profile, graph_version=graph_version,
                duration_seconds=payload["durations"], distance_meters=payload["distances"])


def import_quote_csv(path):
    import csv
    records = []
    with Path(path).open(newline='') as stream:
        for row in csv.DictReader(stream):
            provenance = {key: row.pop(key) for key in (
                'source_url','observed_at','valid_from','valid_to','confidence_note','version')}
            provenance['data_kind'] = 'quotation'
            records.append(Quote.model_validate(dict(row, provenance=provenance)))
    ids = [record.id for record in records]
    known = {city['id'] for city in destinations()}
    if len(ids) != len(set(ids)) or any(record.destination_id not in known for record in records):
        raise ValueError('Повторные идентификаторы или неизвестное направление')
    return records
