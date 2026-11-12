import importlib.metadata
from pathlib import Path
import platform
import sys
from typing import Dict

from .catalog_api import CatalogService, JsonCatalogProvider, catalog_coverage
from .data import ROOT, load_weather
from .domain import TripRequest
from .forecast import WeatherModel
from .integrity import verify_release


PACKAGES = ['numpy','pandas','scikit-learn','scipy','matplotlib','pydantic','requests','ortools','streamlit',
            'PuLP','networkx','rdflib','pytest','openpyxl']


def inspect_environment(verify_manifest: bool = False) -> Dict[str, object]:
    errors = []
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f'Пакет не установлен: {package}')
    if not (3,9) <= sys.version_info[:2] < (3,13):
        errors.append('Нужен Python 3.9–3.12; для передачи используется Python 3.11')
    if 'PuLP' in versions:
        import pulp
        if not pulp.PULP_CBC_CMD(msg=False).available():
            errors.append('Недоступен CBC для PuLP')
    if verify_manifest:
        errors.extend(verify_release(ROOT))
    checks = []
    service = CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),allow_demo=True)
    request = TripRequest.model_validate_json((ROOT/'examples/request.json').read_text())
    for city in service.snapshot.destinations:
        dataset = service.for_trip(request,city.id)
        weather = load_weather(city.id)
        model = WeatherModel.load(ROOT/f'artifacts/models/{city.id}.json')
        import pandas as pd
        forecast = model.predict(pd.date_range(dataset.window.start,dataset.window.end))
        checks.append({'city':city.id,'pois':len(dataset.pois),'weather_rows':len(weather),
                       'forecast_days':len(forecast),'matrix_points':len(dataset.matrix.point_ids)})
    return {'ok':not errors,'python':platform.python_version(),'platform':platform.platform(),
        'architecture':platform.machine(),'project_root':str(ROOT),'versions':versions,
        'demo_data_ready':catalog_coverage(service.snapshot)['demo_data_ready'],'checks':checks,'errors':errors,
        'note':'Проверка среды и снимков не заменяет pytest и полный расчет маршрута'}
