"""Reproduce the AHP, knowledge, MILP and decision-analysis evidence."""
import argparse
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import networkx as nx

from travel_dss.ahp import DEMO_MATRIX, analyze_ahp, aggregate_alternatives
from travel_dss.bi import export_bi_database
from travel_dss.catalog_api import CatalogService, JsonCatalogProvider
from travel_dss.data import ROOT, write_json
from travel_dss.domain import TripRequest
from travel_dss.knowledge import derive_poi_knowledge, KnowledgeBase
from travel_dss.ontology import export_ontology
from travel_dss.optimization_pulp import solve_pulp
from travel_dss.preferences import Criteria, utility
from travel_dss.presentation import export_html
from travel_dss.recommendation import recommend, SearchSettings, RecommendationResult
from travel_dss.routing import make_problem
from travel_dss.scenarios import build_scenarios

OUT = ROOT/'reports/passport'


def prepare():
    ahp = analyze_ahp(DEMO_MATRIX)
    payload = json.loads((ROOT/'examples/request.json').read_text())
    payload.update(weight_method='ahp',ahp_matrix=DEMO_MATRIX,weights=ahp.as_weights().model_dump())
    request = TripRequest.model_validate(payload)
    write_json(ROOT/'examples/ahp_request.json',json.loads(request.model_dump_json()))
    alternatives = {name:Criteria(**dict(zip(('interests','economy','weather','convenience'),row)))
        for name,row in {'Казань':[.9,.5,.6,.7],'Нижний Новгород':[.8,.75,.65,.8],'Сочи':[.7,.65,.9,.6]}.items()}
    write_json(OUT/'ahp.json',{'criteria':ahp.model_dump(),'illustrative_alternatives':
        {key:value.model_dump() for key,value in alternatives.items()},
        'distributive_ahp':aggregate_alternatives(alternatives,ahp.as_weights()),
        'absolute_ratings':{key:utility(value,ahp.as_weights()) for key,value in alternatives.items()},
        'input_origin':'Демонстрационные суждения и полезности; не результаты опроса экспертов'})
    snapshot = JsonCatalogProvider(ROOT/'data/api/demo_catalog.json').load()
    write_json(OUT/'ontology.json',export_ontology(snapshot))
    data = CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),True).for_trip(request,'yaroslavl')
    write_json(OUT/'knowledge.json',{'poi_knowledge':derive_poi_knowledge(data,request,True,rainy_day_share=.6),
        'closure_conflict':KnowledgeBase().infer({'subject':'example-required-closed','open_during_trip':False,'required':True}),
        'weather_note':'rainy_day_share=.6 — сценарное предположение только для иллюстрации R06'})
    print('AHP, OWL и трассировки подготовлены')


def run(backend):
    request = TripRequest.model_validate_json((ROOT/'examples/ahp_request.json').read_text())
    result = recommend(request,settings=SearchSettings(backend=backend),allow_demo=True,use_cache=False)
    write_json(OUT/f'{backend}_recommendation.json',json.loads(result.model_dump_json()))
    (OUT/f'{backend}_recommendation.html').write_text(export_html(result))
    print(backend,[(p.destination_name,round(p.score,5),p.solver.status) for p in result.plans])


def milp():
    request = TripRequest.model_validate_json((ROOT/'examples/ahp_request.json').read_text())
    data = CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),True).for_trip(request,'yaroslavl')
    problem = make_problem(data,request,data.transport_offers[0],data.stay_offers[0],build_scenarios(data,100,42))
    result = solve_pulp(problem,10,output_prefix=OUT/'route_model')
    plan = result.pop('plan')
    write_json(OUT/'milp.json',{**result,'n_pois':problem.n,'days':len(problem.dates),'planning_scenarios':100,
                             'plan':json.loads(plan.model_dump_json()) if plan else None})
    print('MILP',result['status'])


def summarize():
    result = RecommendationResult.model_validate_json((OUT/'cp_sat_recommendation.json').read_text())
    write_json(OUT/'bi_export.json',export_bi_database(OUT/'cp_sat_recommendation.json'))
    summary = []
    for plan in result.plans:
        summary.append({'city':plan.destination_name,'score':plan.score,'criteria':plan.criteria.model_dump(),
            'nominal_cost_rub':plan.nominal_cost_rub,'c90_rub':plan.risk['cost_c90_rub'],
            'success_rate':plan.risk['success_rate'],'wilson_lower':plan.risk['wilson_lower'],
            'poi_count':len(plan.poi_ids),'solver':plan.solver.model_dump(),
            'decision_analysis':plan.decision_analysis,'knowledge_trace':plan.knowledge_trace})
    write_json(OUT/'summary.json',{'plans':summary,'metadata':result.metadata})
    tree = next((p.decision_analysis['tree'] for p in result.plans if p.decision_analysis.get('status')=='evaluated'),None)
    if tree:
        graph = nx.DiGraph()
        graph.add_nodes_from((node['id'],node) for node in tree['nodes'])
        graph.add_edges_from((edge['source'],edge['target']) for edge in tree['edges'])
        positions = {'decision':(0,2),'fixed':(-1,1),'reserve':(1,1),
                     'fixed_open':(-1.5,0),'fixed_closed':(-.5,0),'reserve_open':(.5,0),'reserve_closed':(1.5,0)}
        labels = {'decision':'Choose strategy','fixed':'Fixed itinerary','reserve':'Prepare backup'}
        labels.update({node['id']:node['id'].split('_')[-1]+f"\nEU={node['expected_utility']:.3f}"
                       for node in tree['nodes'] if node['kind']=='terminal'})
        fig,ax = plt.subplots(figsize=(12,5))
        nx.draw_networkx(graph,positions,labels=labels,node_size=3000,node_color='#d8eae5',font_size=9,ax=ax)
        nx.draw_networkx_edge_labels(graph,positions,edge_labels={(e['source'],e['target']):e['probability']
            for e in tree['edges'] if 'probability' in e},ax=ax)
        ax.axis('off')
        fig.tight_layout()
        fig.savefig(OUT/'decision_tree.png',dpi=150)
        plt.close(fig)
    print('Сводка и BI-витрины обновлены')


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('step',choices=['prepare','cp_sat','pulp','milp','summarize'])
    step = parser.parse_args().step
    OUT.mkdir(parents=True,exist_ok=True)
    if step in ('cp_sat','pulp'): run(step)
    else: globals()[step]()
