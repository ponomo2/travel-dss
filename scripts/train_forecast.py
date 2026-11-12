import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/travel-dss-matplotlib')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from travel_dss.data import ROOT, destinations, load_weather, write_json
from travel_dss.forecast import train_and_evaluate

report = {}
fig, axes = plt.subplots(6, 2, figsize=(15, 21), constrained_layout=True)
for row, city in enumerate(destinations()):
    model, metrics, predictions = train_and_evaluate(load_weather(city['id']))
    report[city['id']] = metrics
    model.save(ROOT / f'artifacts/models/{city["id"]}.json')
    selected = predictions[metrics['selected']]
    target = ROOT / f'reports/forecast/{city["id"]}.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(target, index=False)
    ax, errors = axes[row]
    ax.plot(selected.date, selected.actual_c, alpha=.6, lw=.7, label='ERA5 actual')
    ax.plot(selected.date, selected.prediction_c, lw=1, label=metrics['selected'])
    ax.fill_between(selected.date, selected.lower_c, selected.upper_c, alpha=.2, label='80% empirical interval')
    ax.set(title=city['id'], ylabel='Daily max temperature (C)')
    ax.legend(fontsize=8)
    errors.hist(selected.error_c, bins=30)
    errors.set(title='Test residuals, 2025', xlabel='Actual - predicted (C)', ylabel='Days')
    print(city['id'], metrics['selected'], metrics['test_2025'][metrics['selected']]['all'], flush=True)
write_json(ROOT / 'reports/forecast/metrics.json', report)
fig.savefig(ROOT / 'reports/forecast/evaluation.png', dpi=130)
