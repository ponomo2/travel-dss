import argparse
from travel_dss.data import destinations, download_weather

parser = argparse.ArgumentParser()
parser.add_argument("--city")
args = parser.parse_args()
for city in destinations():
    if not args.city or args.city == city["id"]:
        print(city["id"], download_weather(city), flush=True)
