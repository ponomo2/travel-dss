from datetime import datetime
from typing import List, Sequence

from .domain import POI, Quote, TripRequest


def precheck_catalog(request: TripRequest, pois: Sequence[POI], quotes: Sequence[Quote], now: datetime,
                     allow_demo: bool = False) -> List[str]:
    issues = []
    index = {poi.id: poi for poi in pois}
    for poi_id in request.required_poi_ids:
        if poi_id not in index:
            issues.append(f"Обязательный объект {poi_id} отсутствует в каталоге")
        elif not (index[poi_id].usable(request.departure.date(), allow_demo)
                  and index[poi_id].usable(request.return_by.date(), allow_demo)):
            issues.append(f"Доступность и цена обязательного объекта {poi_id} не подтверждены")
    if not any(poi.usable(request.departure.date(), allow_demo)
               and poi.usable(request.return_by.date(), allow_demo) for poi in pois):
        issues.append("Нет подтвержденных объектов для построения маршрута")
    for kind, label in [('transport', 'транспорт'), ('stay', 'проживание')]:
        if not any(quote.kind == kind and quote.usable(now, allow_demo=allow_demo)
                   and (quote.start.date(), quote.end.date()) == (request.departure.date(), request.return_by.date()) for quote in quotes):
            issues.append(f"Нет актуальных котировок: {label}")
    return issues
