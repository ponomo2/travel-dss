from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import validate_weather, write_json


def seasonal_day(dates):
    dates = pd.DatetimeIndex(dates)
    return np.array([(pd.Timestamp(2000, d.month, d.day) - pd.Timestamp(2000, 1, 1)).days for d in dates])


def features(dates):
    dates = pd.DatetimeIndex(dates)
    phase = seasonal_day(dates) / 366
    trend = (dates - pd.Timestamp("2000-01-01")).days.to_numpy() / 365.25
    return np.column_stack([trend] + [f(2*np.pi*h*phase) for h in range(1, 4) for f in (np.sin, np.cos)])


class SeasonalBaseline:
    def fit(self, frame):
        phases = seasonal_day(frame.date)
        values = frame.temperature_c.to_numpy()
        self.daily_means = np.array([values[np.minimum(abs(phases-day), 366-abs(phases-day)) <= 15].mean()
                                     for day in range(366)])
        if not np.isfinite(self.daily_means).all():
            raise ValueError("Недостаточно данных для сезонной модели")
        return self

    def predict(self, dates):
        return self.daily_means[seasonal_day(dates)]


class SeasonalRidge:
    def __init__(self, alpha=10):
        self.alpha = alpha
        self.pipeline = make_pipeline(StandardScaler(), Ridge(alpha=alpha))

    def fit(self, frame):
        self.pipeline.fit(features(frame.date), frame.temperature_c)
        return self

    def predict(self, dates):
        scaler, ridge = self.pipeline.steps[0][1], self.pipeline.steps[1][1]
        normalized = scaler.transform(features(dates))
        prediction = np.einsum("ij,j->i", normalized, ridge.coef_) + ridge.intercept_
        if not np.isfinite(prediction).all():
            raise ValueError("Модель вернула нечисловой прогноз")
        return prediction


def mae(actual, prediction):
    return float(np.mean(abs(np.asarray(actual) - prediction)))


def season_mask(dates, season):
    months = pd.DatetimeIndex(dates).month
    return np.isin(months, {"winter": [12, 1, 2], "spring": [3, 4, 5],
                            "summer": [6, 7, 8], "autumn": [9, 10, 11]}[season])


@dataclass
class WeatherModel:
    point: object
    calibration: pd.DataFrame
    name: str
    trained_until: str

    def predict(self, dates):
        dates = pd.DatetimeIndex(dates)
        if len(dates) == 0 or dates.min() <= self.calibration.date.max():
            raise ValueError("Прогноз требует непустого набора будущих дат")
        return self._predict_intervals(dates)

    def _predict_intervals(self, dates):
        prediction = self.point.predict(dates)
        phase = seasonal_day(self.calibration.date)
        quantiles = []
        for day in seasonal_day(dates):
            delta = np.minimum(abs(phase-day), 366-abs(phase-day))
            residual = self.calibration.residual.to_numpy()[delta <= 45]
            quantiles.append(np.quantile(residual, [0.1, 0.9]))
        quantiles = np.asarray(quantiles)
        return pd.DataFrame({"date": dates, "prediction_c": prediction,
                             "lower_c": prediction+quantiles[:, 0], "upper_c": prediction+quantiles[:, 1]})

    def sample(self, dates, count=1000, seed=42):
        dates = pd.DatetimeIndex(dates)
        if not 2 <= len(dates) <= 5 or not dates.equals(pd.date_range(dates.min(), periods=len(dates))):
            raise ValueError("Блок погоды должен состоять из 2–5 последовательных дней")
        if count < 1:
            raise ValueError("Число сценариев должно быть положительным")
        self.predict(dates)
        history = self.calibration
        phase = seasonal_day(history.date)
        delta = abs(phase - seasonal_day(dates[:1])[0])
        indices = np.flatnonzero(np.minimum(delta, 366-delta) <= 45)
        indices = indices[indices + len(dates) <= len(history)]
        if not len(indices):
            raise ValueError("Недостаточно калибровочных блоков")
        rng = np.random.default_rng(seed)
        samples = []
        for start in rng.choice(indices, size=count):
            block = history.iloc[start:start+len(dates)]
            samples.append(np.column_stack([self.point.predict(dates)+block.residual.to_numpy(),
                                             block.rain_mm, block.wind_kmh]))
        return np.array(samples)

    def save(self, path):
        if self.name == "baseline":
            state = {"daily_means": self.point.daily_means.tolist()}
        else:
            scaler, ridge = self.point.pipeline.steps[0][1], self.point.pipeline.steps[1][1]
            state = {"alpha": self.point.alpha, "mean": scaler.mean_.tolist(),
                     "scale": scaler.scale_.tolist(), "coef": ridge.coef_.tolist(), "intercept": float(ridge.intercept_)}
        calibration = self.calibration.assign(date=self.calibration.date.dt.strftime("%Y-%m-%d"))
        write_json(path, {"schema_version": 1, "name": self.name, "trained_until": self.trained_until,
                          "state": state, "calibration": calibration.to_dict(orient="records")})

    @classmethod
    def load(cls, path):
        import json
        payload = json.loads(Path(path).read_text())
        if payload["schema_version"] != 1:
            raise ValueError("Неизвестная версия модели")
        state = payload["state"]
        if payload["name"] == "baseline":
            point = SeasonalBaseline()
            point.daily_means = np.array(state["daily_means"])
        elif payload["name"] == "ridge":
            point = SeasonalRidge(state["alpha"])
            scaler, ridge = point.pipeline.steps[0][1], point.pipeline.steps[1][1]
            scaler.mean_, scaler.scale_ = np.array(state["mean"]), np.array(state["scale"])
            scaler.var_, scaler.n_features_in_ = scaler.scale_**2, len(state["mean"])
            ridge.coef_, ridge.intercept_, ridge.n_features_in_ = np.array(state["coef"]), state["intercept"], len(state["coef"])
        else:
            raise ValueError("Неизвестный тип модели")
        calibration = pd.DataFrame(payload["calibration"])
        calibration["date"] = pd.to_datetime(calibration.date)
        return cls(point, calibration, payload["name"], payload["trained_until"])


def fit_calibrated(frame, name, alpha=10):
    train = frame[frame.date.dt.year <= 2023]
    calibration = frame[frame.date.dt.year == 2024].copy()
    point = (SeasonalBaseline() if name == "baseline" else SeasonalRidge(alpha)).fit(train)
    calibration["residual"] = calibration.temperature_c - point.predict(calibration.date)
    return WeatherModel(point, calibration, name, "2023-12-31")


def train_and_evaluate(frame):
    validate_weather(frame, "2015-01-01", "2025-12-31")
    rows = []
    for year in (2022, 2023):
        train, valid = frame[frame.date.dt.year < year], frame[frame.date.dt.year == year]
        for alpha in (0.1, 1, 10, 100, 1000):
            model = SeasonalRidge(alpha).fit(train)
            rows.append({"year": year, "alpha": alpha, "mae": mae(valid.temperature_c, model.predict(valid.date))})
    best_alpha = min((0.1, 1, 10, 100, 1000), key=lambda a: np.mean([r["mae"] for r in rows if r["alpha"] == a]))
    validation = {}
    for name in ("baseline", "ridge"):
        errors = []
        for year in (2022, 2023):
            train, valid = frame[frame.date.dt.year < year], frame[frame.date.dt.year == year]
            point = (SeasonalBaseline() if name == "baseline" else SeasonalRidge(best_alpha)).fit(train)
            errors.append(mae(valid.temperature_c, point.predict(valid.date)))
        validation[name] = float(np.mean(errors))
    models = {name: fit_calibrated(frame, name, best_alpha) for name in validation}
    coverage = {}
    for name, model in models.items():
        # Interval calibration is assessed only on 2024; 2025 does not select the model.
        calibration = model.calibration
        predicted = model._predict_intervals(calibration.date)
        coverage[name] = float(((calibration.temperature_c.to_numpy() >= predicted.lower_c)
                                & (calibration.temperature_c.to_numpy() <= predicted.upper_c)).mean())
    selected = ("ridge" if validation["ridge"] <= 0.95 * validation["baseline"]
                and abs(coverage["ridge"] - 0.8) <= abs(coverage["baseline"] - 0.8) + 0.03 else "baseline")
    test = frame[frame.date.dt.year == 2025]
    metrics, predictions = {}, {}
    for name, model in models.items():
        predicted = model.predict(test.date)
        predicted["actual_c"] = test.temperature_c.to_numpy()
        predicted["error_c"] = predicted.actual_c - predicted.prediction_c
        predictions[name] = predicted
        groups = {"all": np.ones(len(test), dtype=bool)}
        groups.update({s: season_mask(test.date, s) for s in ("winter", "spring", "summer", "autumn")})
        metrics[name] = {}
        for group, mask in groups.items():
            part = predicted[mask]
            metrics[name][group] = {"n": len(part), "mae_c": mae(part.actual_c, part.prediction_c),
                "coverage_80": float(((part.actual_c >= part.lower_c) & (part.actual_c <= part.upper_c)).mean()),
                "interval_width_c": float((part.upper_c-part.lower_c).mean())}
    for group in metrics["baseline"]:
        metrics["ridge"][group]["skill"] = 1 - metrics["ridge"][group]["mae_c"] / metrics["baseline"][group]["mae_c"]
    return models[selected], {"selected": selected, "best_alpha": best_alpha,
        "validation_mae": validation, "calibration_coverage": coverage, "grid": rows, "test_2025": metrics}, predictions
