import argparse
from pathlib import Path

from travel_dss.bi import export_bi_database
from travel_dss.data import ROOT


if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Обновить SQLite-витрины Superset')
    parser.add_argument('--source',type=Path,default=ROOT/'reports/passport/cp_sat_recommendation.json')
    args = parser.parse_args()
    print(export_bi_database(args.source))
