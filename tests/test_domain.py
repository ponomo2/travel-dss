import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from travel_dss.data import ROOT
from travel_dss.domain import Provenance, Quote, TripRequest, Weights


def request_data():
    return json.loads((ROOT / 'examples/request.json').read_text())


@pytest.mark.parametrize('change', [
    {'budget_rub': 0}, {'budget_rub': float('nan')}, {'good_cost_rub': 45000},
    {'return_by': '2027-04-16T07:00:00+03:00'}, {'return_by': '2027-04-22T22:00:00+03:00'},
    {'departure': '2027-04-16T08:00:00'}, {'temperature_min_c': 30, 'temperature_max_c': 10},
    {'required_poi_ids': ['a'], 'excluded_poi_ids': ['a']}, {'required_poi_ids': ['a', 'a']},
    {'weights': {'interests': 0, 'economy': 0, 'weather': 0, 'convenience': 0}},
    {'max_walk_km_day': -1}, {'unknown': 1}])
def test_invalid_requests(change):
    with pytest.raises(ValidationError):
        TripRequest(**dict(request_data(), **change))


def test_request_days_and_weights():
    assert TripRequest(**request_data()).days == 4
    assert Weights.from_points(interests=45, economy=25, weather=15, convenience=15) == Weights()
    with pytest.raises(ValueError):
        Weights.from_points(interests=0, economy=0, weather=0, convenience=0)


def test_quote_expiry_and_future_observation():
    now = datetime.fromisoformat('2026-11-12T12:00:00+03:00')
    provenance = Provenance(source_url='https://example.org/offer', observed_at=now,
                            valid_from='2027-04-16', valid_to='2027-04-19', data_kind='quotation',
                            confidence_note='test fixture', version='1')
    quote = Quote(id='q', destination_id='kazan', kind='transport', total_rub=10000, available=True,
                  start='2027-04-16T09:00:00+03:00', end='2027-04-19T20:00:00+03:00', provenance=provenance)
    assert quote.usable(now)
    assert not quote.usable(now + timedelta(hours=25))
    assert not quote.usable(now - timedelta(hours=1))


def test_opening_window_last_admission_and_midnight():
    from travel_dss.domain import OpeningInterval
    provenance = Provenance(source_url='https://example.org/hours', observed_at='2026-11-12T12:00:00+03:00',
        valid_from='2027-04-16', valid_to='2027-04-16', data_kind='expert', confidence_note='fixture',version='1')
    window = OpeningInterval(poi_id='a',start='2027-04-16T22:00:00+03:00',end='2027-04-17T01:00:00+03:00',
                             last_admission='2027-04-17T00:00:00+03:00',provenance=provenance)
    assert window.fits(datetime.fromisoformat('2027-04-16T23:00:00+03:00'),90)
    assert not window.fits(datetime.fromisoformat('2027-04-17T00:10:00+03:00'),30)


def test_missing_catalog_is_explained():
    from travel_dss.validation import precheck_catalog
    request = TripRequest(**request_data())
    issues = precheck_catalog(request,[],[],datetime.fromisoformat('2026-11-12T12:00:00+03:00'))
    assert len(issues) == 3
