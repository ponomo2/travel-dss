"""Versioned catalog contract shared by file and HTTP providers."""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Protocol
from zoneinfo import ZoneInfo

import requests
from pydantic import Field, model_validator

from .data import import_osrm
from .domain import DataKind, OpeningInterval, POI, Provenance, Quote, Record, TripRequest


class PriceRange(Record):
    minimum: float = Field(ge=0)
    typical: float = Field(ge=0)
    maximum: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self):
        if not self.minimum <= self.typical <= self.maximum:
            raise ValueError("Нужен диапазон minimum ≤ typical ≤ maximum")
        return self


class Location(Record):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Destination(Location):
    timezone: str

    @model_validator(mode="after")
    def validate_timezone(self):
        ZoneInfo(self.timezone)
        return self


class TripWindow(Record):
    id: str
    start: date
    end: date

    @model_validator(mode="after")
    def validate_dates(self):
        if not 2 <= (self.end - self.start).days + 1 <= 5:
            raise ValueError("Окно поездки должно занимать 2–5 дней")
        return self


class TransportOffer(Record):
    quote: Quote
    window_id: str
    mode: Literal["flight", "train"]
    destination_arrival: datetime
    return_departure: datetime
    terminal: Location
    price_rub: PriceRange
    conditions: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_itinerary(self):
        if self.destination_arrival.utcoffset() is None or self.return_departure.utcoffset() is None:
            raise ValueError("Все участки транспорта должны содержать часовой пояс")
        if not self.quote.start < self.destination_arrival < self.return_departure < self.quote.end:
            raise ValueError("Несовместимое расписание транспорта")
        if self.quote.kind != "transport" or self.price_rub.typical != self.quote.total_rub:
            raise ValueError("Транспортный тариф не согласован с котировкой")
        return self


class StayOffer(Record):
    quote: Quote
    window_id: str
    location: Location
    nights: int = Field(gt=0)
    nightly_rub: float = Field(gt=0)
    price_rub: PriceRange
    conditions: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_price(self):
        if (self.quote.kind != "stay" or self.price_rub.typical != self.quote.total_rub
                or abs(self.nightly_rub * self.nights - self.quote.total_rub) > 0.01
                or (self.quote.end.date() - self.quote.start.date()).days != self.nights):
            raise ValueError("Стоимость или число ночей проживания не согласованы")
        return self


class DailyCosts(Record):
    destination_id: str
    food_per_person_day_rub: PriceRange
    transfer_one_way_rub: PriceRange
    transfer_one_way_minutes: PriceRange
    provenance: Provenance


class TravelMatrix(Record):
    destination_id: str
    point_ids: List[str]
    distances_m: List[List[Optional[float]]]
    durations_seconds: List[List[Optional[float]]]
    provenance: Provenance
    method: str

    @model_validator(mode="after")
    def validate_values(self):
        import_osrm({"code": "Ok", "distances": self.distances_m,
                     "durations": self.durations_seconds}, self.point_ids, "foot", self.provenance.version)
        for i, (distance_row, duration_row) in enumerate(zip(self.distances_m, self.durations_seconds)):
            if distance_row[i] != 0 or duration_row[i] != 0:
                raise ValueError("Диагональ матрицы должна равняться нулю")
            if any((distance is None) != (duration is None)
                   for distance, duration in zip(distance_row, duration_row)):
                raise ValueError("Недостижимость должна совпадать в обеих матрицах")
        return self


class CatalogSnapshot(Record):
    schema_version: Literal["1.0"]
    mode: Literal["demo", "live"]
    provider: str = Field(min_length=1)
    generated_at: datetime
    currency: Literal["RUB"]
    notice: str = Field(min_length=1)
    destinations: List[Destination] = Field(min_length=1)
    trip_windows: List[TripWindow] = Field(min_length=1)
    pois: List[POI] = Field(min_length=1)
    opening_intervals: List[OpeningInterval]
    poi_prices_rub: Dict[str, PriceRange]
    transport_offers: List[TransportOffer]
    stay_offers: List[StayOffer]
    daily_costs: List[DailyCosts]
    matrices: List[TravelMatrix]
    original_poi_sources: Dict[str, str]

    @model_validator(mode="after")
    def validate_relations(self):
        if self.generated_at.utcoffset() is None:
            raise ValueError("generated_at должен содержать часовой пояс")
        groups = [self.destinations, self.trip_windows, self.pois]
        for group in groups:
            if len({item.id for item in group}) != len(group):
                raise ValueError("Повторный идентификатор в каталоге")
        cities = {city.id: city for city in self.destinations}
        windows = {window.id: window for window in self.trip_windows}
        pois = {poi.id: poi for poi in self.pois}
        offers = self.transport_offers + self.stay_offers
        if len({offer.quote.id for offer in offers}) != len(offers):
            raise ValueError("Повторный идентификатор предложения")
        if set(self.poi_prices_rub) != set(pois) or set(self.original_poi_sources) != set(pois):
            raise ValueError("Цены и источники должны покрывать каждый объект")
        records = list(self.pois) + self.daily_costs + self.matrices + [o.quote for o in offers]
        if any(record.destination_id not in cities for record in records):
            raise ValueError("Ссылка на неизвестное направление")
        provenance = [record.provenance for record in records] + [w.provenance for w in self.opening_intervals]
        if self.mode == "live" and any(p.data_kind == DataKind.synthetic for p in provenance):
            raise ValueError("Синтетические записи запрещены в live-каталоге")
        if self.mode == "demo" and any(p.data_kind != DataKind.synthetic for p in provenance):
            raise ValueError("Все записи демонстрационного API должны быть явно синтетическими")
        if any(p.observed_at > self.generated_at for p in provenance):
            raise ValueError("Запись получена позже формирования снимка")
        for poi in self.pois:
            if (poi.admission_rub != self.poi_prices_rub[poi.id].typical
                    or poi.indoor is None or not poi.opening_hours):
                raise ValueError("Неполные атрибуты объекта или несогласованная цена")
            if self.mode == "demo" and poi.verified:
                raise ValueError("Демонстрационный объект нельзя помечать проверенным")
        for offer in offers:
            window = windows.get(offer.window_id)
            if window is None or (offer.quote.start.date(), offer.quote.end.date()) != (window.start, window.end):
                raise ValueError("Предложение не соответствует окну поездки")
        for interval in self.opening_intervals:
            if interval.poi_id not in pois:
                raise ValueError("Расписание ссылается на неизвестный объект")
            local_date = interval.start.astimezone(ZoneInfo(cities[pois[interval.poi_id].destination_id].timezone)).date()
            if not any(w.start <= local_date <= w.end for w in self.trip_windows):
                raise ValueError("Расписание вне покрытия каталога")
            if not interval.provenance.valid_from <= local_date <= interval.provenance.valid_to:
                raise ValueError("Расписание вне периода действия источника")
        for poi_id in pois:
            intervals = sorted((w for w in self.opening_intervals if w.poi_id == poi_id), key=lambda w: w.start)
            if any(left.end > right.start for left, right in zip(intervals, intervals[1:])):
                raise ValueError("Перекрывающиеся окна объекта")
        if {c.destination_id for c in self.daily_costs} != set(cities) or len(self.daily_costs) != len(cities):
            raise ValueError("Нужен один набор дневных расходов на каждый город")
        if {m.destination_id for m in self.matrices} != set(cities) or len(self.matrices) != len(cities):
            raise ValueError("Нужна одна матрица на каждый город")
        for matrix in self.matrices:
            expected = {poi.id for poi in self.pois if poi.destination_id == matrix.destination_id}
            expected.update(o.location.id for o in self.stay_offers if o.quote.destination_id == matrix.destination_id)
            expected.update(o.terminal.id for o in self.transport_offers if o.quote.destination_id == matrix.destination_id)
            if set(matrix.point_ids) != expected:
                raise ValueError("Матрица не покрывает объекты, жилье и терминалы города")
        return self


class CatalogProvider(Protocol):
    def load(self) -> CatalogSnapshot:
        ...


class JsonCatalogProvider:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> CatalogSnapshot:
        return CatalogSnapshot.model_validate_json(self.path.read_text(encoding="utf-8"))


class HttpCatalogProvider:
    def __init__(self, url: str, session: Optional[requests.Session] = None, timeout: float = 30):
        if not url.startswith("https://"):
            raise ValueError("Для внешнего API нужен HTTPS URL")
        self.url = url
        self.session = session or requests.Session()
        self.timeout = timeout

    def load(self) -> CatalogSnapshot:
        response = self.session.get(self.url, timeout=self.timeout, headers={"Accept": "application/json"})
        response.raise_for_status()
        return CatalogSnapshot.model_validate(response.json())


class TripDataset(Record):
    mode: Literal["demo", "live"]
    as_of: datetime
    notice: str
    destination: Destination
    window: TripWindow
    pois: List[POI]
    opening_intervals: List[OpeningInterval]
    poi_prices_rub: Dict[str, PriceRange]
    transport_offers: List[TransportOffer]
    stay_offers: List[StayOffer]
    daily_costs: DailyCosts
    matrix: TravelMatrix

    def opening_windows(self, poi_id: str, on: date) -> List[OpeningInterval]:
        if poi_id not in {poi.id for poi in self.pois}:
            raise ValueError("Объект не входит в выборку")
        if not self.window.start <= on <= self.window.end:
            raise ValueError("Дата вне выбранного окна")
        tz = ZoneInfo(self.destination.timezone)
        return [interval for interval in self.opening_intervals
                if interval.poi_id == poi_id and interval.start.astimezone(tz).date() == on]

    def travel_between(self, origin_id: str, destination_id: str) -> Dict[str, Optional[float]]:
        indices = {point: i for i, point in enumerate(self.matrix.point_ids)}
        if origin_id not in indices or destination_id not in indices:
            raise ValueError("Точка отсутствует в матрице")
        i, j = indices[origin_id], indices[destination_id]
        return {"distance_m": self.matrix.distances_m[i][j], "duration_seconds": self.matrix.durations_seconds[i][j]}

    def estimate_cost(self, transport_id: str, stay_id: str, poi_ids: List[str]) -> PriceRange:
        if len(set(poi_ids)) != len(poi_ids) or not set(poi_ids) <= set(self.poi_prices_rub):
            raise ValueError("В смете есть повторные или неизвестные объекты")
        transport = next((o for o in self.transport_offers if o.quote.id == transport_id), None)
        stay = next((o for o in self.stay_offers if o.quote.id == stay_id), None)
        if transport is None or stay is None:
            raise ValueError("Предложение отсутствует в выборке")
        days = (self.window.end - self.window.start).days + 1
        prices = [(transport.price_rub, 1), (stay.price_rub, 1),
                  (self.daily_costs.food_per_person_day_rub, days),
                  (self.daily_costs.transfer_one_way_rub, 2)]
        prices.extend((self.poi_prices_rub[poi_id], 1) for poi_id in poi_ids)
        return PriceRange(**{field: round(sum(getattr(price, field) * count for price, count in prices), 2)
                             for field in PriceRange.model_fields})


class CatalogService:
    def __init__(self, provider: CatalogProvider, allow_demo: bool = False):
        self.snapshot = provider.load()
        self.allow_demo = allow_demo
        if self.snapshot.mode == "demo" and not allow_demo:
            raise ValueError("Для демонстрационного API явно укажите allow_demo=True")

    def for_trip(self, request: TripRequest, destination_id: str, as_of: Optional[datetime] = None) -> TripDataset:
        snapshot = self.snapshot
        as_of = as_of or (snapshot.generated_at if snapshot.mode == "demo" else datetime.now(timezone.utc))
        if as_of.utcoffset() is None or as_of < snapshot.generated_at:
            raise ValueError("Некорректный момент оценки снимка")
        destination = next((city for city in snapshot.destinations if city.id == destination_id), None)
        if destination is None:
            raise ValueError("Направление не поддерживается")
        tz = ZoneInfo("Europe/Moscow")
        dates = (request.departure.astimezone(tz).date(), request.return_by.astimezone(tz).date())
        window = next((w for w in snapshot.trip_windows if (w.start, w.end) == dates), None)
        if window is None:
            raise ValueError("На эти даты каталог не содержит предложений")
        pois = [p for p in snapshot.pois if p.destination_id == destination_id
                and p.id not in request.excluded_poi_ids
                and p.usable(window.start, self.allow_demo) and p.usable(window.end, self.allow_demo)]
        intervals = [w for w in snapshot.opening_intervals if w.poi_id in {p.id for p in pois}
                     and window.start <= w.start.astimezone(ZoneInfo(destination.timezone)).date() <= window.end]
        open_ids = {w.poi_id for w in intervals}
        pois = [p for p in pois if p.id in open_ids]
        missing = set(request.required_poi_ids) - {p.id for p in pois}
        if missing:
            raise ValueError("Обязательные объекты недоступны: " + ", ".join(sorted(missing)))
        costs = next(c for c in snapshot.daily_costs if c.destination_id == destination_id)
        matrix = next(m for m in snapshot.matrices if m.destination_id == destination_id)
        if any(not (record.provenance.valid_from <= window.start and window.end <= record.provenance.valid_to)
               for record in (costs, matrix)):
            raise ValueError("Расходы или матрица не действуют на выбранные даты")
        transport = [o for o in snapshot.transport_offers if o.quote.destination_id == destination_id
                     and o.window_id == window.id and o.quote.usable(as_of, allow_demo=self.allow_demo)
                     and o.quote.start >= request.departure and o.quote.end <= request.return_by]
        stays = [o for o in snapshot.stay_offers if o.quote.destination_id == destination_id
                 and o.window_id == window.id and o.quote.usable(as_of, allow_demo=self.allow_demo)]
        if not pois or not transport or not stays:
            raise ValueError("Нет объектов или доступных актуальных предложений для заданных ограничений")
        return TripDataset(mode=snapshot.mode, as_of=as_of, notice=snapshot.notice,
            destination=destination, window=window, pois=pois, opening_intervals=intervals,
            poi_prices_rub={p.id: snapshot.poi_prices_rub[p.id] for p in pois},
            transport_offers=transport, stay_offers=stays,
            daily_costs=costs, matrix=matrix)


def save_snapshot(snapshot: CatalogSnapshot, path: Path) -> None:
    from .data import write_json
    import json
    write_json(path, json.loads(snapshot.model_dump_json()))


def store_snapshot(snapshot: CatalogSnapshot, database: Path) -> None:
    import sqlite3
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS api_snapshot (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO api_snapshot VALUES (1, ?)", (snapshot.model_dump_json(),))
        for table, records in (("pois", snapshot.pois),
                               ("quotes", [o.quote for o in snapshot.transport_offers + snapshot.stay_offers])):
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, destination_id TEXT NOT NULL, payload TEXT NOT NULL)")
            connection.execute(f"DELETE FROM {table}")
            connection.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)",
                                   [(r.id, r.destination_id, r.model_dump_json()) for r in records])


class SqliteCatalogProvider:
    def __init__(self, database: Path):
        self.database = Path(database)

    def load(self) -> CatalogSnapshot:
        import sqlite3
        if not self.database.is_file():
            raise ValueError("Файл каталога не существует")
        with sqlite3.connect(self.database) as connection:
            row = connection.execute("SELECT payload FROM api_snapshot WHERE id=1").fetchone()
        if row is None:
            raise ValueError("В базе отсутствует снимок API")
        return CatalogSnapshot.model_validate_json(row[0])


def catalog_coverage(snapshot: CatalogSnapshot) -> dict:
    cities = []
    for city in snapshot.destinations:
        pois = [p for p in snapshot.pois if p.destination_id == city.id]
        offers = [o for o in snapshot.transport_offers + snapshot.stay_offers if o.quote.destination_id == city.id]
        available = [o for o in offers if o.quote.usable(snapshot.generated_at, allow_demo=snapshot.mode == "demo")]
        counts = {window.id: {kind: sum(o.window_id == window.id and o.quote.kind == kind for o in available)
                              for kind in ("transport", "stay")} for window in snapshot.trip_windows}
        covered_pois = all(p.usable(window.start, snapshot.mode == "demo")
                           and p.usable(window.end, snapshot.mode == "demo")
                           and any(w.poi_id == p.id and window.start <= w.start.date() <= window.end
                                   for w in snapshot.opening_intervals)
                           for p in pois for window in snapshot.trip_windows)
        cities.append({"destination": city.id, "pois": len(pois), "confirmed_real_pois": sum(p.confirmed for p in pois),
                       "offers_by_window": counts, "calendar_complete": covered_pois})
    ready = all(20 <= city["pois"] <= 30 and city["calendar_complete"]
                and all(all(count >= 1 for count in kinds.values()) for kinds in city["offers_by_window"].values())
                for city in cities)
    return {"stage": 2, "status": "complete_demo" if snapshot.mode == "demo" and ready else "requires_review",
            "mode": snapshot.mode, "as_of": snapshot.generated_at.isoformat(), "currency": snapshot.currency,
            "poi_count": len(snapshot.pois), "opening_intervals": len(snapshot.opening_intervals),
            "transport_offers": len(snapshot.transport_offers), "stay_offers": len(snapshot.stay_offers),
            "daily_cost_sets": len(snapshot.daily_costs), "matrices": len(snapshot.matrices),
            "demo_data_ready": ready and snapshot.mode == "demo", "confirmed_real_data_ready": False,
            "notice": snapshot.notice, "cities": cities}
