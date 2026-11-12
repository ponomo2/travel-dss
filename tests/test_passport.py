import json
from types import SimpleNamespace

import numpy as np
import pytest
from rdflib import Graph, RDF, RDFS

from conftest import small_problem
from test_optimization import exhaustive
from travel_dss.ahp import DEMO_MATRIX, analyze_ahp, aggregate_alternatives
from travel_dss.catalog_api import JsonCatalogProvider
from travel_dss.data import ROOT
from travel_dss.domain import TripRequest
from travel_dss.knowledge import KnowledgeBase, derive_poi_knowledge
from travel_dss.ontology import export_ontology, TRAVEL
from travel_dss.optimization_pulp import solve_pulp
from travel_dss.decision_analysis import analyze_closure_strategy
from travel_dss.preferences import Criteria


def test_ahp_eigenvector_consistency_and_request():
    result = analyze_ahp(DEMO_MATRIX)
    np.testing.assert_allclose(np.einsum('ij,j->i',DEMO_MATRIX,result.priorities),
                               np.array(result.priorities)*result.lambda_max,atol=1e-10)
    assert result.consistency_ratio == pytest.approx(.00383811194)
    payload = json.loads((ROOT/'examples/request.json').read_text())
    payload.update(weight_method='ahp',ahp_matrix=DEMO_MATRIX,weights=result.as_weights().model_dump())
    assert TripRequest.model_validate(payload).weight_method == 'ahp'
    payload['weights'] = dict(interests=.25,economy=.25,weather=.25,convenience=.25)
    with pytest.raises(ValueError,match='Веса не соответствуют'):
        TripRequest.model_validate(payload)
    with pytest.raises(ValueError): analyze_ahp([[1,2],[2,1]])
    with pytest.raises(ValueError): analyze_ahp([[1,float('nan')],[1,1]])
    inconsistent = analyze_ahp([[1,9,1/9],[1/9,1,9],[9,1/9,1]])
    assert not inconsistent.acceptable


def test_ahp_ratio_synthesis():
    result = aggregate_alternatives({'a':Criteria(interests=.9,economy=.5,weather=.6,convenience=.7),
        'b':Criteria(interests=.8,economy=.75,weather=.65,convenience=.8)},analyze_ahp(DEMO_MATRIX).as_weights())
    assert sum(result['scores'].values()) == pytest.approx(1)
    assert all(row['consistency_ratio'] < 1e-10 for row in result['local_matrices'].values())


def test_excel_formulas_and_cached_values_match_python():
    from openpyxl import load_workbook
    path = ROOT/'reports/passport/AHP.xlsx'
    workbook = load_workbook(path,data_only=True)
    computed = [workbook['Расчет'].cell(55,column).value for column in range(2,6)]
    np.testing.assert_allclose(computed,analyze_ahp(DEMO_MATRIX).priorities,atol=1e-10)
    assert workbook['Результат']['C20'].value == 1
    formulas = load_workbook(path,data_only=False)
    assert formulas['Расчет']['B6'].value == '=1/C5'
    assert formulas['Результат']['B6'].value.startswith('=')


def test_knowledge_forward_inference_unknown_and_conflict(trip_data,tmp_path):
    base = KnowledgeBase()
    inferred = base.infer({'synthetic':True,'open_during_trip':False,'required':True})
    assert inferred['facts']['confirmation_required'] is True
    assert inferred['facts']['eligible'] is False and inferred['facts']['request_conflict'] is True
    assert {row['rule_id'] for row in inferred['trace']} == {'R01','R02','R07'}
    assert 'eligible' not in base.infer({})['facts']
    with pytest.raises(ValueError,match='Противоречие'):
        base.infer({'open_during_trip':False,'eligible':True})
    request,data = trip_data
    reports = derive_poi_knowledge(data,request,True)['entities']
    assert all(row['facts']['eligible'] for row in reports.values())
    snapshot = JsonCatalogProvider(ROOT/'data/api/demo_catalog.json').load()
    target = tmp_path/'travel.owl'
    export_ontology(snapshot,target)
    graph = Graph().parse(target)
    assert len(list(graph.subjects(RDF.type,TRAVEL.POI))) == 173
    assert (TRAVEL.SyntheticPOI,RDFS.subClassOf,TRAVEL.NeedsConfirmationPOI) in graph


@pytest.mark.parametrize('seed',range(20))
def test_pulp_matches_exact_enumeration(trip_data,seed):
    problem = small_problem(*trip_data,seed)
    result = solve_pulp(problem,4)
    assert result['validated'] and result['status'] == 'OPTIMAL'
    assert result['score'] == pytest.approx(exhaustive(problem),abs=1e-6)
    assert result['objective'] == pytest.approx(result['score'],abs=1e-6)


def test_pulp_budget_infeasible(trip_data):
    problem = small_problem(*trip_data,1)
    problem.request = problem.request.model_copy(update={'budget_rub':1000,'good_cost_rub':500})
    assert solve_pulp(problem,1)['status'] == 'INFEASIBLE'


def test_decision_tree_threshold_and_missing_backup():
    plan = SimpleNamespace(risk={'success_rate':.95},score=.8,stress_tests=[])
    assert analyze_closure_strategy(plan)['status'] == 'no_validated_backup'
    plan.stress_tests = [{'status':'BACKUP_VALIDATED','closed_poi_id':'x',
                         'backup':{'risk':{'success_rate':.9},'score':.7}}]
    result = analyze_closure_strategy(plan)
    expected = .01/(.9*.7+.1*(-.2)-(-.2))
    assert result['break_even_probability'] == pytest.approx(expected)
    at_threshold = analyze_closure_strategy(plan,closure_probability=expected)['sensitivity'][0]
    assert at_threshold['fixed_expected_utility'] == pytest.approx(at_threshold['reserve_expected_utility'])
    assert result['sensitivity'][0]['preferred'] == 'fixed'
    assert result['sensitivity'][-1]['preferred'] == 'reserve'
    with pytest.raises(ValueError): analyze_closure_strategy(plan,closure_probability=1.1)
