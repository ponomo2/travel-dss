from dataclasses import dataclass
from math import sqrt
from typing import Dict, List

import numpy as np
import pandas as pd
from scipy.special import ndtr

from .catalog_api import PriceRange, TripDataset
from .data import ROOT
from .forecast import WeatherModel


def triangular_quantile(price: PriceRange, uniforms: np.ndarray) -> np.ndarray:
    low, mode, high = price.minimum, price.typical, price.maximum
    if high == low:
        return np.full_like(uniforms, low, dtype=float)
    pivot = (mode-low)/(high-low)
    return np.where(uniforms < pivot, low+np.sqrt(uniforms*(high-low)*(mode-low)),
                    high-np.sqrt((1-uniforms)*(high-low)*(high-mode)))


@dataclass
class ScenarioSet:
    seed: int
    weather: np.ndarray
    travel_factors: np.ndarray
    transfer_minutes: np.ndarray
    transport_rub: Dict[str, np.ndarray]
    stay_rub: Dict[str, np.ndarray]
    food_rub: np.ndarray
    transfer_rub: np.ndarray
    admission_rub: np.ndarray
    poi_ids: List[str]

    @property
    def count(self) -> int:
        return len(self.weather)

    def base_costs(self, transport_id: str, stay_id: str) -> np.ndarray:
        return (self.transport_rub[transport_id] + self.stay_rub[stay_id]
                + self.food_rub.sum(axis=1) + self.transfer_rub.sum(axis=1))


def build_scenarios(data: TripDataset, count: int = 100, seed: int = 42) -> ScenarioSet:
    if count < 1:
        raise ValueError('Число сценариев должно быть положительным')
    dates = pd.date_range(data.window.start, data.window.end)
    weather_model = WeatherModel.load(ROOT/f'artifacts/models/{data.destination.id}.json')
    weather = weather_model.sample(dates, count=count, seed=seed)
    rng = np.random.default_rng(seed)
    common = rng.normal(size=count)

    def sample(price, columns=1):
        correlated = .75*common[:, None]+sqrt(1-.75**2)*rng.normal(size=(count, columns))
        return triangular_quantile(price, ndtr(correlated))

    factors = 1+rng.triangular(-.05, 0, .15, size=(count, len(dates)))
    factors += .05*(weather[..., 1] > 2)+.10*(weather[..., 1] > 10)
    transfers = sample(data.daily_costs.transfer_one_way_minutes, 2)
    transfers[:, 0] *= 1+.1*(weather[:, 0, 1] > 10)
    transfers[:, 1] *= 1+.1*(weather[:, -1, 1] > 10)
    admissions = np.column_stack([sample(data.poi_prices_rub[p.id])[:, 0] for p in data.pois])
    return ScenarioSet(seed, weather, factors, transfers,
        {o.quote.id: sample(o.price_rub)[:, 0] for o in data.transport_offers},
        {o.quote.id: sample(o.price_rub)[:, 0] for o in data.stay_offers},
        sample(data.daily_costs.food_per_person_day_rub, len(dates)),
        sample(data.daily_costs.transfer_one_way_rub, 2), admissions, [p.id for p in data.pois])
