import json

import numpy as np
import pytest

from travel_dss.data import ROOT
from travel_dss.presentation import export_html,save_feedback
from travel_dss.recommendation import RecommendationResult,SearchSettings,recommend
from travel_dss.risk import wilson_interval
from travel_dss.scenarios import build_scenarios


def test_scenario_reproducibility_and_cost_dependence(trip_data):
    _,data = trip_data
    first,second = build_scenarios(data,1000,21),build_scenarios(data,1000,21)
    np.testing.assert_array_equal(first.weather,second.weather)
    np.testing.assert_array_equal(first.admission_rub,second.admission_rub)
    t = first.transport_rub[data.transport_offers[0].quote.id]
    h = first.stay_rub[data.stay_offers[0].quote.id]
    assert np.corrcoef(t,h)[0,1] > .3
    low,high = wilson_interval(920,1000)
    assert .901 < low < .903 and high > .92


@pytest.fixture(scope='module')
def system_result(trip_data):
    request,_ = trip_data
    return recommend(request,['kazan'],SearchSettings(pair_seconds=.8,total_seconds=6,
        closure_stress=False,alternatives=False),allow_demo=True,use_cache=False)


def test_recommendation_end_to_end(trip_data,tmp_path,system_result):
    request,_ = trip_data
    result = system_result
    assert result.plans
    for plan in result.plans:
        assert plan.nominal_cost_rub <= request.budget_rub
        assert plan.risk['sample_count'] == 1000
        assert plan.risk['independent_from_planning']
        assert len(plan.poi_ids) == len(set(plan.poi_ids))
        assert all(day.walk_m <= request.max_walk_km_day*1000 for day in plan.days)
    html = export_html(result)
    assert '<!doctype html>' in html and 'Казань' in html
    path = tmp_path/'feedback.sqlite'
    save_feedback(path,result.plans[0].id,'selected','Понятный маршрут')
    assert path.exists()
    with pytest.raises(ValueError):
        save_feedback(path,result.plans[0].id,'invalid','')


def test_data_failures_and_no_silent_changes(trip_data):
    request,_ = trip_data
    changed = request.model_copy(update={'required_poi_ids':['missing-object']})
    result = recommend(changed,['kazan'],SearchSettings(total_seconds=2),allow_demo=True,use_cache=False)
    assert not result.plans
    assert result.request.required_poi_ids == ['missing-object']
    assert result.diagnostics[0]['status'] == 'DATA_UNAVAILABLE'


def test_streamlit_form():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value == 'Куда поехать'
    assert any(button.label == 'Подобрать путешествие' for button in app.button)


def test_streamlit_renders_saved_result(system_result):
    from streamlit.testing.v1 import AppTest
    result = system_result
    app = AppTest.from_file(str(ROOT/'app.py'),default_timeout=30)
    app.session_state['result'] = result
    app.run()
    assert not app.exception
    assert app.dataframe
    assert any('Нижний Новгород' in x.label or 'Казань' in x.label for x in app.expander)


def test_html_escapes_catalog_text(system_result):
    from travel_dss.recommendation import RecommendationResult
    result = system_result.model_copy(deep=True)
    result.plans[0].destination_name = '<script>alert(1)</script>'
    rendered = export_html(result)
    assert '<script>alert(1)</script>' not in rendered
    assert '&lt;script&gt;' in rendered


def test_low_budget_returns_checked_repair_when_available(trip_data):
    request,_ = trip_data
    request = request.model_copy(update={'budget_rub':1000,'good_cost_rub':500})
    result = recommend(request,['kazan'],SearchSettings(pair_seconds=.8,total_seconds=6,
        alternatives=False,closure_stress=False),allow_demo=True,use_cache=False)
    assert not result.plans
    assert result.request.budget_rub == 1000
    verified = [s for s in result.suggestions if s['verified']]
    assert verified
    assert verified[0]['plan']['nominal_cost_rub'] <= verified[0]['changed_budget_rub']
