from math import isfinite
from typing import Dict, Sequence

import numpy as np

from .domain import POI, Record, TripRequest, Weights
from pydantic import Field


def clip(value):
    if not isfinite(value):
        raise ValueError("Критерий должен быть конечным числом")
    return min(1.0, max(0.0, value))


class Criteria(Record):
    interests: float = Field(ge=0, le=1)
    economy: float = Field(ge=0, le=1)
    weather: float = Field(ge=0, le=1)
    convenience: float = Field(ge=0, le=1)


def interest_utility(pois: Sequence[POI], request: TripRequest):
    if len({p.id for p in pois}) != len(pois):
        raise ValueError("Повторное посещение нельзя засчитывать дважды")
    return sum(weight * clip(sum(p.themes.model_dump()[theme] for p in pois) / request.days)
               for theme, weight in request.interests.model_dump().items())


def evaluate_criteria(pois, request, expected_cost_rub, transfer_hours, walk_km, weather_comfort):
    if min(expected_cost_rub, transfer_hours, walk_km) < 0:
        raise ValueError("Расходы и перемещения не могут быть отрицательными")
    good = request.good_cost_rub if request.good_cost_rub is not None else request.budget_rub * 0.6
    return Criteria(interests=interest_utility(pois, request),
                    economy=clip((request.budget_rub - expected_cost_rub) / (request.budget_rub - good)),
                    weather=weather_comfort,
                    convenience=1 - 0.5 * clip(transfer_hours / 24)
                    - 0.5 * clip(walk_km / (request.days * request.max_walk_km_day)))


def contributions(criteria: Criteria, weights: Weights) -> Dict[str, float]:
    return {key: value * weights.model_dump()[key] for key, value in criteria.model_dump().items()}


def utility(criteria, weights):
    return sum(contributions(criteria, weights).values())


def explain_comparison(first, second, weights):
    left, right = contributions(first, weights), contributions(second, weights)
    deltas = {key: left[key] - right[key] for key in left}
    labels = {"interests": "интересы", "economy": "экономичность", "weather": "погода", "convenience": "удобство"}
    return {"difference": sum(deltas.values()), "contributions": deltas,
            "text": "; ".join(f"{labels[k]}: {v * 100:+.2f} балла" for k, v in deltas.items())}


def rank(candidates, weights):
    return sorted(candidates, key=lambda key: (-utility(candidates[key], weights), key))


def pareto_front(candidates):
    def dominates(left, right):
        a, b = list(left.model_dump().values()), list(right.model_dump().values())
        return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))
    return [key for key, value in candidates.items()
            if not any(dominates(other, value) for name, other in candidates.items() if name != key)]


def sensitivity(candidates, start: Weights, end: Weights, steps=101):
    if steps < 2:
        raise ValueError("Нужно не менее двух шагов")
    return [{"fraction": float(t), "ranking": rank(candidates, Weights(**{
        key: (1-t) * value + t * end.model_dump()[key] for key, value in start.model_dump().items()}))}
            for t in np.linspace(0, 1, steps)]


def weather_comfort(samples, request):
    values = np.asarray(samples, dtype=float)
    if values.ndim != 3 or values.shape[-1] != 3 or 0 in values.shape or not np.isfinite(values).all():
        raise ValueError("Нужны конечные сценарии формы (сценарии, дни, температура/осадки/ветер)")
    if (values[..., 1:] < 0).any():
        raise ValueError("Осадки и ветер не могут быть отрицательными")
    suitable = ((values[..., 0] >= request.temperature_min_c) & (values[..., 0] <= request.temperature_max_c)
                & (values[..., 1] <= request.max_rain_mm) & (values[..., 2] <= request.max_wind_kmh))
    return float(suitable.mean())
