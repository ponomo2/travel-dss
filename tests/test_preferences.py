import pytest
import numpy as np

from travel_dss.data import ROOT
from travel_dss.domain import TripRequest, Weights
from travel_dss.preferences import Criteria, contributions, evaluate_criteria, explain_comparison, pareto_front, rank, sensitivity, utility, weather_comfort


def candidates():
    return {'kazan': Criteria(interests=.9, economy=.5, weather=.6, convenience=.7),
            'nizhny': Criteria(interests=.8, economy=.75, weather=.65, convenience=.8)}


def test_ranking_and_explanation():
    values = candidates()
    weights = Weights()
    assert rank(values, weights)[0] == 'nizhny'
    assert utility(values['nizhny'], weights) == pytest.approx(.765)
    result = explain_comparison(values['nizhny'], values['kazan'], weights)
    assert result['difference'] == pytest.approx(.04)
    assert sum(result['contributions'].values()) == pytest.approx(.04)
    end = Weights(interests=.7, economy=.1, weather=.1, convenience=.1)
    points = sensitivity(values, weights, end)
    assert points[-1]['ranking'][0] == 'kazan'
    assert points[57]['ranking'][0] == 'nizhny'
    assert points[58]['ranking'][0] == 'kazan'


def test_fixed_scales_and_dominance():
    values = candidates()
    before = contributions(values['kazan'], Weights())
    values['bad'] = Criteria(interests=0, economy=0, weather=0, convenience=0)
    assert set(pareto_front(values)) == {'kazan', 'nizhny'}
    assert contributions(values['kazan'], Weights()) == before


def test_criteria_and_weather():
    request = TripRequest.model_validate_json((ROOT / 'examples/request.json').read_text())
    result = evaluate_criteria([], request, 35000, 0, 0, .5)
    assert result.economy == .5 and result.interests == 0 and result.convenience == 1
    assert weather_comfort(np.array([[[20, 0, 10], [0, 0, 10]]]), request) == .5
    with pytest.raises(ValueError):
        weather_comfort(np.zeros((0, 4, 3)), request)


def test_interest_saturation_and_duplicate_protection():
    from travel_dss.data import import_records
    from travel_dss.domain import Distribution, POI
    from travel_dss.preferences import interest_utility
    request = TripRequest.model_validate_json((ROOT/'examples/request.json').read_text())
    request = request.model_copy(update={'interests':Distribution(culture=1,history=0,nature=0,food=0)})
    original = import_records(ROOT/'data/processed/pois/kazan.json', POI)[0]
    points = [original.model_copy(update={'id':str(i), 'themes':request.interests}) for i in range(6)]
    assert interest_utility(points[:4],request) == 1
    assert interest_utility(points,request) == 1
    with pytest.raises(ValueError):
        interest_utility([original,original], request)
