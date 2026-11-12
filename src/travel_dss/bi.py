import json
import sqlite3
from pathlib import Path

import pandas as pd

from .data import ROOT


def export_bi_database(recommendation_path: Path, target: Path = ROOT/'data/processed/bi.sqlite') -> dict:
    payload = json.loads(Path(recommendation_path).read_text())
    target.parent.mkdir(parents=True,exist_ok=True)
    plans = []
    visits = []
    for plan in payload['plans']:
        plans.append({'plan_id':plan['id'],'city':plan['destination_name'],'score':plan['score'],
            'nominal_cost_rub':plan['nominal_cost_rub'],'expected_cost_rub':plan['expected_cost_rub'],
            'cost_c50_rub':plan['risk']['cost_c50_rub'],'cost_c90_rub':plan['risk']['cost_c90_rub'],
            'success_rate':plan['risk']['success_rate'],'wilson_lower':plan['risk']['wilson_lower'],
            'solver_status':plan['solver']['status'],'mode':plan['mode'],**plan['criteria']})
        for day in plan['days']:
            for visit in day['visits']:
                visits.append({'plan_id':plan['id'],'city':plan['destination_name'],'date':day['day'],
                    'poi_id':visit['poi_id'],'name':visit['name'],'start':visit['start'],'end':visit['end'],
                    'kind':visit['kind'],'walk_before_m':visit['walk_before_m']})
    forecasts = []
    for path in sorted((ROOT/'reports/forecast').glob('*.csv')):
        frame = pd.read_csv(path)
        frame['city'] = path.stem
        frame.rename(columns={'actual_c':'temperature_actual_c','prediction_c':'temperature_predicted_c'},inplace=True)
        forecasts.append(frame)
    metrics = json.loads((ROOT/'reports/forecast/metrics.json').read_text())
    quality = [{'city':city,'model':record['selected'],**record['test_2025'][record['selected']]['all']}
               for city,record in metrics.items()]
    with sqlite3.connect(target) as database:
        pd.DataFrame(plans).to_sql('recommendations',database,if_exists='replace',index=False)
        pd.DataFrame(visits).to_sql('visits',database,if_exists='replace',index=False)
        pd.concat(forecasts,ignore_index=True).to_sql('forecast_test',database,if_exists='replace',index=False,
                                                  dtype={'date':'TIMESTAMP'})
        pd.DataFrame(quality).to_sql('forecast_quality',database,if_exists='replace',index=False)
        database.execute('CREATE INDEX IF NOT EXISTS forecast_city_date ON forecast_test(city,date)')
    return {'recommendations':len(plans),'visits':len(visits),'forecast_rows':sum(len(f) for f in forecasts),'quality_rows':len(quality)}
