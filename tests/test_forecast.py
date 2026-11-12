import numpy as np
import pandas as pd
import pytest

from travel_dss.data import ROOT, load_weather, validate_weather
from travel_dss.forecast import SeasonalBaseline, SeasonalRidge, WeatherModel, fit_calibrated, seasonal_day


def test_seasonal_day_leap_year():
    assert seasonal_day(['2023-03-01'])[0] == seasonal_day(['2024-03-01'])[0]
    assert seasonal_day(['2024-02-29'])[0] + 1 == seasonal_day(['2024-03-01'])[0]


@pytest.mark.parametrize('model_type', [SeasonalBaseline, SeasonalRidge])
def test_synthetic_seasonality(model_type):
    dates = pd.date_range('2015-01-01', '2023-12-31')
    frame = pd.DataFrame({'date': dates, 'temperature_c': 10+15*np.sin(2*np.pi*seasonal_day(dates)/366)})
    model = model_type().fit(frame)
    future = pd.date_range('2025-01-01', periods=365)
    expected = 10+15*np.sin(2*np.pi*seasonal_day(future)/366)
    assert np.mean(abs(model.predict(future) - expected)) < .3


@pytest.mark.parametrize('city', ['saint_petersburg','kazan','nizhny_novgorod','yaroslavl','kaliningrad','sochi'])
def test_real_artifacts(city, tmp_path):
    frame = load_weather(city)
    assert len(frame) == 4018
    model = WeatherModel.load(ROOT / f'artifacts/models/{city}.json')
    dates = pd.date_range('2027-04-16', periods=4)
    prediction = model.predict(dates)
    assert np.isfinite(prediction.prediction_c).all()
    assert (prediction.lower_c <= prediction.upper_c).all()
    samples = model.sample(dates, count=50)
    assert samples.shape == (50, 4, 3)
    np.testing.assert_array_equal(samples, model.sample(dates, count=50))
    path = tmp_path / 'model.json'
    model.save(path)
    np.testing.assert_allclose(WeatherModel.load(path).predict(dates).prediction_c, prediction.prediction_c)
    with pytest.raises(ValueError):
        model.predict(pd.date_range('2024-01-01', periods=4))


def test_ridge_roundtrip_and_no_future_leakage(tmp_path):
    frame = load_weather('kazan')
    model = fit_calibrated(frame, 'ridge')
    altered = frame.copy()
    altered.loc[altered.date.dt.year == 2025, 'temperature_c'] += 100
    other = fit_calibrated(altered, 'ridge')
    dates = pd.date_range('2026-04-01', periods=4)
    np.testing.assert_allclose(model.predict(dates).prediction_c, other.predict(dates).prediction_c)
    path = tmp_path/'ridge.json'
    model.save(path)
    np.testing.assert_allclose(WeatherModel.load(path).predict(dates).prediction_c, model.predict(dates).prediction_c)


def test_reject_weather_gaps():
    frame = load_weather('kazan')
    with pytest.raises(ValueError):
        validate_weather(frame.iloc[1:], '2015-01-01', '2025-12-31')
    with pytest.raises(ValueError):
        validate_weather(pd.concat([frame, frame.iloc[:1]]))


def test_selection_is_independent_of_test_targets():
    from travel_dss.forecast import train_and_evaluate
    frame = load_weather('sochi')
    model, report, _ = train_and_evaluate(frame)
    altered = frame.copy()
    altered.loc[altered.date.dt.year == 2025, 'temperature_c'] += 20
    other, second, _ = train_and_evaluate(altered)
    assert report['selected'] == second['selected']
    assert report['validation_mae'] == second['validation_mae']
    dates = pd.date_range('2027-01-22', periods=4)
    np.testing.assert_array_equal(model.sample(dates), other.sample(dates))
