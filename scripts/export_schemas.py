import csv
from travel_dss.data import ROOT, write_json
from travel_dss.domain import OpeningInterval, POI, Quote, TripRequest

for name, model in [('request', TripRequest), ('poi', POI), ('quote', Quote), ('opening_interval', OpeningInterval)]:
    write_json(ROOT / f'data/templates/{name}.schema.json', model.model_json_schema())
write_json(ROOT / 'data/templates/quotes.json', [])
write_json(ROOT / 'data/templates/pois.json', [])
headers = ['id','destination_id','kind','start','end','total_rub','available','source_url','observed_at',
           'valid_from','valid_to','confidence_note','version']
with (ROOT/'data/templates/quotes.csv').open('w', newline='') as file:
    csv.writer(file).writerow(headers)
