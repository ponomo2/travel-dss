from travel_dss.data import ROOT, write_json
from travel_dss.domain import Weights
from travel_dss.preferences import Criteria, explain_comparison, rank, sensitivity

candidates = {
    'Казань': Criteria(interests=.9, economy=.5, weather=.6, convenience=.7),
    'Нижний Новгород': Criteria(interests=.8, economy=.75, weather=.65, convenience=.8),
    'Сочи': Criteria(interests=.7, economy=.65, weather=.9, convenience=.6)}
start = Weights()
end = Weights(interests=.7, economy=.1, weather=.1, convenience=.1)
write_json(ROOT / 'reports/criteria_demo.json', {
    'data_kind': 'synthetic', 'initial_ranking': rank(candidates, start),
    'interest_first_ranking': rank(candidates, end),
    'comparison': explain_comparison(candidates['Нижний Новгород'], candidates['Казань'], start),
    'sensitivity_fixed_routes': sensitivity(candidates, start, end)})

import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/travel-dss-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from travel_dss.preferences import utility

fig, ax = plt.subplots(figsize=(9, 4.5), constrained_layout=True)
labels = {'Казань':'Kazan', 'Нижний Новгород':'Nizhny Novgorod', 'Сочи':'Sochi'}
for city, criteria in candidates.items():
    first, last = utility(criteria, start), utility(criteria, end)
    ax.plot([0, 100], [first, last], marker='o', label=labels[city])
ax.axvline(100*4/7, color='gray', ls='--', label='Leader changes at 57.1%')
ax.set(xlabel='Transition from initial to interest-first weights (%)', ylabel='Utility',
       title='Preference sensitivity: fixed synthetic travel options')
ax.legend()
fig.savefig(ROOT/'reports/criteria_sensitivity.png', dpi=150)
