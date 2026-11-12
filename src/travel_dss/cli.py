import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import requests

from .catalog_api import CatalogService, HttpCatalogProvider, JsonCatalogProvider, catalog_coverage, store_snapshot
from .data import ROOT, destinations, import_records, import_quote_csv, store_catalog
from .domain import POI, Quote, TripRequest
from .forecast import WeatherModel
from .preferences import weather_comfort


def _run_cli():
    parser = argparse.ArgumentParser(description="Travel DSS: маршруты и объяснимые рекомендации")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-request")
    validate.add_argument("path", type=Path)
    forecast = sub.add_parser("forecast")
    forecast.add_argument("city", choices=[d["id"] for d in destinations()])
    forecast.add_argument("start")
    forecast.add_argument("--days", type=int, default=4, choices=range(2, 6))
    forecast.add_argument("--request", type=Path)
    imports = sub.add_parser("import-catalog")
    imports.add_argument("--pois", type=Path, required=True)
    imports.add_argument("--quotes", type=Path, required=True)
    catalog = sub.add_parser("catalog", help="Загрузить данные API для поездки")
    catalog.add_argument("city", choices=[d["id"] for d in destinations()])
    catalog.add_argument("--request", type=Path, default=ROOT / "examples/request.json")
    catalog_source = catalog.add_mutually_exclusive_group()
    catalog_source.add_argument("--source", type=Path, default=ROOT / "data/api/demo_catalog.json")
    catalog_source.add_argument("--url")
    catalog.add_argument("--allow-demo", action="store_true")
    catalog.add_argument("--as-of", help="ISO timestamp; demo defaults to snapshot time")
    catalog.add_argument("--output", type=Path)
    sync = sub.add_parser("sync-catalog", help="Проверить и сохранить полный снимок API в SQLite")
    sync_source = sync.add_mutually_exclusive_group()
    sync_source.add_argument("--source", type=Path, default=ROOT / "data/api/demo_catalog.json")
    sync_source.add_argument("--url")
    sync.add_argument("--allow-demo", action="store_true")
    sync.add_argument("--database", type=Path, default=ROOT / "data/processed/catalog_demo.sqlite")
    plans = sub.add_parser("recommend", help="Подобрать направления и выполнимые маршруты")
    plans.add_argument("--request",type=Path,default=ROOT/"examples/request.json")
    plans.add_argument("--source",type=Path,default=ROOT/"data/api/demo_catalog.json")
    plans.add_argument("--cities",nargs="+")
    plans.add_argument("--allow-demo",action="store_true")
    plans.add_argument("--backend",choices=["cp_sat","pulp"],default="cp_sat")
    plans.add_argument("--seconds",type=float,default=60)
    plans.add_argument("--pair-seconds",type=float,default=2)
    plans.add_argument("--no-cache",action="store_true")
    plans.add_argument("--output",type=Path,default=ROOT/"reports/recommendation.json")
    plans.add_argument("--html",type=Path)
    doctor = sub.add_parser("doctor",help="Проверить среду, данные и модели")
    doctor.add_argument("--verify-manifest",action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        from .doctor import inspect_environment
        report = inspect_environment(args.verify_manifest)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['ok']:
            raise SystemExit(1)
    elif args.command == "recommend":
        from .recommendation import SearchSettings, recommend
        from .presentation import export_html
        from .data import write_json
        request = TripRequest.model_validate_json(args.request.read_text())
        result = recommend(request,args.cities,SearchSettings(backend=args.backend,pair_seconds=args.pair_seconds,total_seconds=args.seconds),
                           args.source,args.allow_demo,use_cache=not args.no_cache)
        write_json(args.output,json.loads(result.model_dump_json()))
        if args.html:
            args.html.parent.mkdir(parents=True,exist_ok=True)
            args.html.write_text(export_html(result),encoding="utf-8")
        print(json.dumps({"plans":[{"city":p.destination_name,"score":p.score,"cost_rub":p.nominal_cost_rub,
            "success_rate":p.risk["success_rate"],"status":p.solver.status} for p in result.plans],
            "elapsed_seconds":result.metadata["elapsed_seconds"],"cache_hit":result.metadata["cache_hit"],
            "output":str(args.output)},ensure_ascii=False,indent=2))
    elif args.command in ("catalog", "sync-catalog"):
        from datetime import datetime
        from .data import write_json
        provider = HttpCatalogProvider(args.url) if args.url else JsonCatalogProvider(args.source)
        service = CatalogService(provider, allow_demo=args.allow_demo)
        if args.command == "sync-catalog":
            store_snapshot(service.snapshot, args.database)
            coverage = catalog_coverage(service.snapshot)
            coverage_path = args.database.with_suffix(".coverage.json")
            write_json(coverage_path, coverage)
            print(json.dumps({"database": str(args.database), "coverage": coverage}, ensure_ascii=False, indent=2))
        else:
            request = TripRequest.model_validate_json(args.request.read_text())
            dataset = service.for_trip(request, args.city, datetime.fromisoformat(args.as_of) if args.as_of else None)
            estimates = [{"transport_id": transport.quote.id, "stay_id": stay.quote.id,
                          "cost_without_admissions": dataset.estimate_cost(transport.quote.id, stay.quote.id, []).model_dump()}
                         for transport in dataset.transport_offers for stay in dataset.stay_offers]
            result = {"mode": dataset.mode, "as_of": dataset.as_of.isoformat(), "notice": dataset.notice,
                      "city": dataset.destination.name, "pois": len(dataset.pois),
                      "opening_intervals": len(dataset.opening_intervals), "base_cost_estimates": estimates,
                      "budget_rub": request.budget_rub,
                      "interpretation": "Смета включает дорогу, все ночи, питание и два трансфера; билеты добавляются после выбора объектов"}
            if args.output:
                write_json(args.output, json.loads(dataset.model_dump_json()))
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "validate-request":
        request = TripRequest.model_validate_json(args.path.read_text())
        print(request.model_dump_json(indent=2))
    elif args.command == "forecast":
        model = WeatherModel.load(ROOT / f"artifacts/models/{args.city}.json")
        dates = pd.date_range(args.start, periods=args.days)
        result = model.predict(dates)
        print(result.to_string(index=False))
        metrics = json.loads((ROOT / "reports/forecast/metrics.json").read_text())[args.city]
        quality = metrics["test_2025"][metrics["selected"]]["all"]
        print(json.dumps({"kind":"seasonal_estimate", "model":model.name,
                          "test_mae_c":quality["mae_c"],
                          "test_interval_coverage":quality["coverage_80"],
                          "nominal_interval_coverage":0.8,
                          "note":"Сезонная оценка; покрытие интервала не гарантировано"}, ensure_ascii=False))
        if args.request:
            request = TripRequest.model_validate_json(args.request.read_text())
            if dates[0].date() != request.departure.date() or len(dates) != request.days:
                raise ValueError("Даты прогноза и анкеты должны совпадать")
            print(json.dumps({"weather_comfort": weather_comfort(model.sample(dates), request),
                              "kind": "seasonal_estimate", "model": model.name}, ensure_ascii=False))
    else:
        pois = import_records(args.pois, POI)
        quotes = (import_quote_csv(args.quotes) if args.quotes.suffix == ".csv"
                  else import_records(args.quotes, Quote))
        store_catalog(pois, quotes, ROOT / "data/processed/catalog.sqlite")
        print(json.dumps({"pois": len(pois), "confirmed": sum(p.confirmed for p in pois), "quotes": len(quotes)}))


def main():
    try:
        _run_cli()
    except (ValueError, OSError, requests.RequestException) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
