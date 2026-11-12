from datetime import date, datetime, timedelta
from enum import Enum
from math import isclose
from typing import Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DataKind(str, Enum):
    reanalysis = "reanalysis"
    quotation = "quotation"
    expert = "expert"
    osm = "osm"
    synthetic = "synthetic"


class Provenance(Record):
    source_url: str = Field(pattern=r"^https?://")
    observed_at: datetime
    valid_from: date
    valid_to: date
    data_kind: DataKind
    confidence_note: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_period(self):
        if self.valid_to < self.valid_from or self.observed_at.utcoffset() is None:
            raise ValueError("Требуются корректный период и observed_at с часовым поясом")
        return self


class Distribution(Record):
    culture: float = Field(default=0.25, ge=0)
    history: float = Field(default=0.25, ge=0)
    nature: float = Field(default=0.25, ge=0)
    food: float = Field(default=0.25, ge=0)

    @model_validator(mode="after")
    def validate_sum(self):
        if not isclose(sum(self.model_dump().values()), 1, abs_tol=1e-8):
            raise ValueError("Доли интересов должны суммироваться в 1")
        return self


class Weights(Record):
    interests: float = Field(default=0.45, ge=0)
    economy: float = Field(default=0.25, ge=0)
    weather: float = Field(default=0.15, ge=0)
    convenience: float = Field(default=0.15, ge=0)

    @model_validator(mode="after")
    def validate_sum(self):
        if not isclose(sum(self.model_dump().values()), 1, abs_tol=1e-8):
            raise ValueError("Веса должны суммироваться в 1")
        return self

    @classmethod
    def from_points(cls, **points):
        if set(points) != set(cls.model_fields) or any(v < 0 for v in points.values()):
            raise ValueError("Нужны неотрицательные баллы всех четырех критериев")
        total = sum(points.values())
        if total <= 0:
            raise ValueError("Сумма баллов должна быть положительной")
        return cls(**{k: v / total for k, v in points.items()})


class TripRequest(Record):
    origin: Literal["Москва"] = "Москва"
    departure: datetime
    return_by: datetime
    budget_rub: float = Field(gt=0)
    interests: Distribution = Field(default_factory=Distribution)
    weights: Weights = Field(default_factory=Weights)
    weight_method: Literal["swing", "ahp"] = "swing"
    ahp_matrix: Optional[List[List[float]]] = None
    temperature_min_c: float = 15
    temperature_max_c: float = 28
    max_rain_mm: float = Field(default=2, ge=0)
    max_wind_kmh: float = Field(default=30, ge=0)
    max_walk_km_day: float = Field(default=10, gt=0)
    active_minutes_day: int = Field(default=360, gt=0, le=1440)
    meal_minutes: int = Field(default=30, gt=0)
    event_buffer_minutes: int = Field(default=10, ge=0)
    min_success_rate: float = Field(default=0.9, gt=0, le=1)
    good_cost_rub: Optional[float] = Field(default=None, ge=0)
    required_poi_ids: List[str] = Field(default_factory=list)
    excluded_poi_ids: List[str] = Field(default_factory=list)

    @property
    def days(self):
        tz = ZoneInfo("Europe/Moscow")
        return (self.return_by.astimezone(tz).date() - self.departure.astimezone(tz).date()).days + 1

    @model_validator(mode="after")
    def validate_request(self):
        if self.weight_method == 'ahp':
            from .ahp import analyze_ahp
            if self.ahp_matrix is None:
                raise ValueError('Для AHP требуется матрица сравнений')
            expected = analyze_ahp(self.ahp_matrix).as_weights()
            if any(not isclose(value,expected.model_dump()[key],abs_tol=1e-8)
                   for key,value in self.weights.model_dump().items()):
                raise ValueError('Веса не соответствуют матрице AHP')
        elif self.ahp_matrix is not None:
            raise ValueError('Матрица AHP задана при другом способе взвешивания')
        if self.departure.utcoffset() is None or self.return_by.utcoffset() is None:
            raise ValueError("Время поездки должно содержать часовой пояс")
        if self.return_by <= self.departure or not 2 <= self.days <= 5:
            raise ValueError("Поездка должна занимать 2–5 календарных дней")
        if self.temperature_min_c > self.temperature_max_c:
            raise ValueError("Некорректный диапазон температуры")
        if self.good_cost_rub is not None and self.good_cost_rub >= self.budget_rub:
            raise ValueError("Хорошая цена должна быть ниже бюджета")
        if set(self.required_poi_ids) & set(self.excluded_poi_ids):
            raise ValueError("Объект одновременно обязателен и исключен")
        if len(set(self.required_poi_ids)) != len(self.required_poi_ids):
            raise ValueError("Обязательные объекты не должны повторяться")
        return self


class POI(Record):
    id: str
    destination_id: str
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    themes: Distribution
    duration_min: int = Field(gt=0)
    duration_max: int = Field(gt=0)
    indoor: Optional[bool] = None
    admission_rub: Optional[float] = Field(default=None, ge=0)
    opening_hours: Optional[str] = None
    verified: bool = False
    provenance: Provenance

    @model_validator(mode="after")
    def validate_duration(self):
        if self.duration_max < self.duration_min:
            raise ValueError("Неверный диапазон длительности")
        return self

    @property
    def confirmed(self):
        return (self.verified and self.provenance.data_kind != DataKind.synthetic
                and self.admission_rub is not None and self.opening_hours is not None)

    def usable(self, on: date, allow_demo: bool = False) -> bool:
        accepted = self.confirmed or (allow_demo and self.provenance.data_kind == DataKind.synthetic)
        return (accepted and self.admission_rub is not None and self.opening_hours is not None
                and self.provenance.valid_from <= on <= self.provenance.valid_to)


class Quote(Record):
    id: str
    destination_id: str
    kind: Literal["transport", "stay"]
    start: datetime
    end: datetime
    total_rub: float = Field(gt=0)
    available: bool
    provenance: Provenance

    @model_validator(mode="after")
    def validate_quote(self):
        if self.start.utcoffset() is None or self.end.utcoffset() is None or self.end <= self.start:
            raise ValueError("Котировка требует корректных времени и часовых поясов")
        if self.provenance.data_kind not in (DataKind.quotation, DataKind.synthetic):
            raise ValueError("Допустимы котировки quotation или явно синтетические данные")
        return self

    def usable(self, now: datetime, ttl_hours: int = 24, allow_demo: bool = False):
        return ((self.provenance.data_kind != DataKind.synthetic or allow_demo)
                and now.utcoffset() is not None and self.available
                and timedelta(0) <= now - self.provenance.observed_at <= timedelta(hours=ttl_hours)
                and self.provenance.valid_from <= self.start.date()
                and self.end.date() <= self.provenance.valid_to)


class OpeningInterval(Record):
    poi_id: str
    start: datetime
    end: datetime
    last_admission: Optional[datetime] = None
    provenance: Provenance

    @model_validator(mode="after")
    def validate_window(self):
        if self.start.utcoffset() is None or self.end.utcoffset() is None or self.end <= self.start:
            raise ValueError("Некорректное окно работы")
        if self.last_admission is not None:
            if self.last_admission.utcoffset() is None or not self.start <= self.last_admission <= self.end:
                raise ValueError("Последний вход вне окна работы")
        return self

    def fits(self, arrival: datetime, duration_minutes: int):
        return (duration_minutes > 0 and self.start <= arrival
                and arrival + timedelta(minutes=duration_minutes) <= self.end
                and (self.last_admission is None or arrival <= self.last_admission))
