from datetime import datetime, time
import json
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from .catalog_api import CatalogService, JsonCatalogProvider
from .data import ROOT
from .domain import Distribution, TripRequest, Weights
from .ahp import analyze_ahp,DEMO_MATRIX
from .presentation import LABELS, export_html, itinerary_rows, save_feedback
from .recommendation import SearchSettings, recommend


@st.cache_resource
def catalog_service():
    return CatalogService(JsonCatalogProvider(ROOT/'data/api/demo_catalog.json'),allow_demo=True)


def coordinate_plot(plan,data):
    positions = {p.id:(p.longitude,p.latitude) for p in data.pois}
    hotel = next(s for s in data.stay_offers if s.quote.id == plan.stay_id).location
    positions['__meal__'] = (hotel.longitude,hotel.latitude)
    fig,ax = plt.subplots(figsize=(8,5),layout='constrained')
    for number,day in enumerate(plan.days):
        coords = [(hotel.longitude,hotel.latitude)]+[positions[v.poi_id] for v in day.visits]+[(hotel.longitude,hotel.latitude)]
        ax.plot([p[0] for p in coords],[p[1] for p in coords],'-o',label=f'{day.day}',markersize=4)
        for i,point in enumerate(coords[1:-1]):
            ax.annotate(str(i+1),point,fontsize=8)
    ax.scatter([hotel.longitude],[hotel.latitude],marker='s',s=90,c='black',label='База')
    ax.set(xlabel='Долгота',ylabel='Широта',title='Координатная схема порядка посещений')
    ax.legend(fontsize=8)
    return fig


def render_result(result):
    if not result.plans:
        st.warning('Не найдено проверенных маршрутов. Ограничения не ослаблялись.')
        for index,suggestion in enumerate(result.suggestions):
            st.write(suggestion['message'])
            st.caption(suggestion.get('note',''))
            if suggestion.get('verified'):
                st.download_button('Проверенное решение при измененном ограничении',
                    json.dumps(suggestion['plan'],ensure_ascii=False,indent=2),file_name='changed-budget-plan.json',key=f'repair-{index}')
    else:
        if any(d.get('status') in ('UNKNOWN','NOT_RUN_TIME_LIMIT') for d in result.diagnostics):
            st.warning('Для части комплектов поиск не завершен в лимит времени; сравнение относится к найденным вариантам.')
        st.subheader('Сравнение вариантов')
        st.dataframe(pd.DataFrame([{'Направление':p.destination_name,'Балл / 100':round(p.score*100,1),
            'Смета, ₽':round(p.nominal_cost_rub),'C90, ₽':round(p.risk['cost_c90_rub']),
            'Успех сценариев':f'{p.risk["success_rate"]:.1%}',
            'Устойчивый по модели':p.risk['robust_by_model'],'Объекты':len(p.poi_ids)} for p in result.plans]),hide_index=True)
        st.caption('Устойчивость — нижняя граница 95%-го интервала доли успешных сценариев не ниже вашего порога. '
                   'Это оценка по принятым предположениям, а не гарантия поездки.')
        if result.metadata['fewer_than_three']:
            st.info('Найдено меньше трех достаточно различных вариантов; одинаковые маршруты не дублируются.')
        for index,plan in enumerate(result.plans):
            with st.expander(f'{index+1}. {plan.destination_name} · {plan.score*100:.1f}/100',expanded=index==0):
                data = catalog_service().for_trip(result.request,plan.destination_id)
                transport = next(t for t in data.transport_offers if t.quote.id == plan.transport_id)
                stay = next(s for s in data.stay_offers if s.quote.id == plan.stay_id)
                st.write(f'Выезд из Москвы: {transport.quote.start:%d.%m %H:%M %z}; прибытие: '
                         f'{transport.destination_arrival:%d.%m %H:%M %z}. Обратно: '
                         f'{transport.return_departure:%d.%m %H:%M %z}, дома {transport.quote.end:%d.%m %H:%M %z}.')
                st.write(f'{stay.location.name}, {stay.nights} ночи. Заезд {stay.quote.start:%H:%M}, выезд {stay.quote.end:%H:%M}.')
                status_note = 'Оптимальность для этого пакета доказана' if plan.solver.status == 'OPTIMAL' else 'Допустимый маршрут; оптимальность не доказана'
                gap_note = f'{plan.solver.relative_gap:.1%}' if plan.solver.relative_gap is not None else 'граница недоступна'
                st.caption(f'{status_note}. Статус {plan.solver.status}; разрыв: {gap_note}.')
                criterion_labels = {'interests':'Интересы','economy':'Экономичность','weather':'Погода','convenience':'Удобство'}
                st.dataframe(pd.DataFrame([{'Критерий':criterion_labels[k],'Полезность':round(v,3)} for k,v in plan.criteria.model_dump().items()]),hide_index=True)
                st.dataframe(pd.DataFrame([{'Статья':LABELS[k],'Рубли':value} for k,value in plan.cost_breakdown.items()]),hide_index=True)
                st.write(f'Средняя сценарная стоимость: {plan.expected_cost_rub:,.0f} ₽; '
                         f'C50: {plan.risk["cost_c50_rub"]:,.0f} ₽; C90: {plan.risk["cost_c90_rub"]:,.0f} ₽.')
                st.write(f'Интервал успешности: {plan.risk["wilson_lower"]:.1%}–{plan.risk["wilson_upper"]:.1%}. '
                         f'Независимая проверка: {plan.risk["sample_count"]} сценариев.')
                st.dataframe(pd.DataFrame(itinerary_rows(plan)),hide_index=True)
                for day in plan.days:
                    st.caption(f'{day.day}: начало {day.start:%H:%M}, возврат к базе {day.end:%H:%M}; '
                               f'всего {day.walk_m/1000:.1f} км, последний переход {day.return_minutes} мин.')
                fig = coordinate_plot(plan,data)
                st.pyplot(fig)
                plt.close(fig)
                st.caption('Линии показывают порядок посещений, а не геометрию улиц. Схема работает без сети. '
                           'Источник координат: © OpenStreetMap contributors (ODbL).')
                if not plan.risk['robust_by_model']:
                    st.warning('Риск выше выбранного порога по независимой проверке; вариант не отмечен устойчивым.')
                if plan.stress_tests:
                    for stress in plan.stress_tests:
                        st.write('Закрытие объекта:',stress['closed_poi_id'],stress['status'])
                        st.caption(stress.get('explanation',''))
                        if 'backup' in stress:
                            st.write(f'Разница стоимости: {stress["cost_difference_rub"]:+,.0f} ₽; '
                                     f'разница полезности: {stress["score_difference"]*100:+.1f} балла.')
                            st.download_button('Резервный маршрут JSON',data=json.dumps(stress['backup'],ensure_ascii=False,indent=2),
                                               file_name=f'backup-{plan.id}.json',key='backup-'+plan.id)
                else:
                    st.caption('Перепланирование при закрытии не выполнено в пределах лимита расчета.')
                if plan.decision_analysis and plan.decision_analysis.get('status') == 'evaluated':
                    analysis = plan.decision_analysis
                    st.write('Дерево стратегий: подготовить резерв или сохранить исходный план')
                    threshold = analysis['break_even_probability']
                    if threshold is not None:
                        st.caption(f'При принятой цене подготовки резерв выгоднее при вероятности закрытия выше {threshold:.1%}. '
                                   'Частота закрытий не измерялась; вероятности таблицы — сценарные предположения.')
                    st.dataframe(pd.DataFrame(analysis['sensitivity']),hide_index=True)
                if plan.knowledge_trace:
                    st.caption('Вывод базы знаний: '+'; '.join(t['explanation'] for t in plan.knowledge_trace['trace']))
                with st.form('feedback-'+plan.id):
                    decision = st.radio('Ваш выбор',['Выбрать','Отклонить'],horizontal=True,key='decision-'+plan.id)
                    reason = st.text_input('Причина (необязательно)',key='reason-'+plan.id)
                    if st.form_submit_button('Сохранить выбор'):
                        save_feedback(ROOT/'data/processed/feedback.sqlite',plan.id,'selected' if decision == 'Выбрать' else 'rejected',reason)
                        st.success('Выбор сохранен локально')
        for explanation in result.comparisons:
            st.write('Почему отличаются баллы:',explanation['text'])
    st.download_button('Скачать результат JSON',result.model_dump_json(indent=2),file_name='travel-plans.json')
    st.download_button('Скачать отчет HTML',export_html(result),file_name='travel-plans.html',mime='text/html')
    with st.expander('Источники, риски и параметры расчета'):
        st.json(result.metadata)
        st.dataframe(pd.DataFrame(result.diagnostics))
        st.caption('Цены, часы, предложения и дополнительные связи сети демонстрационные. '
                   'Погода — сезонная модель ERA5/Open-Meteo; интервалы недостаточно калиброваны для некоторых городов. '
                   'Статус FEASIBLE не доказывает оптимальность. Подробности — reports/forecast/MODEL_CARD.md.')


def main():
    st.set_page_config(page_title='Куда поехать',page_icon='🧭',layout='wide')
    st.title('Куда поехать')
    st.markdown('[BI: качество прогноза и сравнение маршрутов в Apache Superset](http://127.0.0.1:8088/superset/dashboard/travel-dss/)')
    st.write('Сравните направления и получите маршрут под свои интересы, бюджет и время.')
    st.info('Учебный MVP: цены, расписания и предложения смоделированы. Окончательный выбор остается за вами.')
    service = catalog_service()
    snapshot = service.snapshot
    with st.form('trip-request'):
        left,right = st.columns(2)
        with left:
            window = st.selectbox('Даты поездки',snapshot.trip_windows,format_func=lambda w:f'{w.start:%d.%m.%Y} — {w.end:%d.%m.%Y}')
            budget = st.number_input('Бюджет на всю поездку, ₽',min_value=1000,value=45000,step=1000)
            selected_cities = st.multiselect('Направления',snapshot.destinations,default=snapshot.destinations,format_func=lambda c:c.name)
            walking = st.slider('Максимум пешком в день, км',1,25,10)
            active = st.slider('Дневной лимит с обедом и ожиданием, минут',120,600,360,30)
            risk = st.slider('Требуемая доля успешных сценариев, %',80,99,90)
        with right:
            st.write('Интересы: распределите баллы')
            interest_values = [st.number_input(label,min_value=0,value=value,key='interest-'+key)
                for key,label,value in [('culture','Культура',45),('history','История',30),('nature','Природа',15),('food','Гастрономия',10)]]
            st.write('Приоритеты улучшений критериев: распределите баллы')
            weight_values = [st.number_input(label,min_value=0,value=value,key='weight-'+key)
                for key,label,value in [('interests','Соответствие интересам',45),('economy','Экономичность',25),('weather','Погодный комфорт',15),('convenience','Удобство',15)]]
        backend_label = st.selectbox('Метод оптимизации',['OR-Tools CP-SAT','PuLP / CBC'])
        weight_method = st.selectbox('Способ задания весов',['Баллы','AHP: парные сравнения'])
        with st.expander('Матрица AHP: значения больше 1 означают приоритет первого критерия'):
            matrix = [row[:] for row in DEMO_MATRIX]
            names = ['Интересы','Экономичность','Погода','Удобство']
            for i in range(4):
                for j in range(i+1,4):
                    value = st.number_input(f'{names[i]} / {names[j]}',min_value=1/9,max_value=9.0,
                                             value=float(DEMO_MATRIX[i][j]),key=f'ahp-{i}-{j}')
                    matrix[i][j],matrix[j][i] = value,1/value
        temp = st.slider('Комфортный максимум температуры днем, °C',-20,40,(10,25))
        rain = st.number_input('Допустимые осадки в день, мм',min_value=0.0,value=2.0)
        wind = st.number_input('Допустимый ветер, км/ч',min_value=0.0,value=30.0)
        city_pois = [p for p in snapshot.pois if p.destination_id in {c.id for c in selected_cities}]
        required = st.multiselect('Обязательные объекты',city_pois,format_func=lambda p:p.name)
        excluded = st.multiselect('Исключить объекты',city_pois,format_func=lambda p:p.name)
        submitted = st.form_submit_button('Подобрать путешествие',type='primary')
    st.caption('Начало прогулок не раньше 09:00; первый день — после приезда и заезда. Обед 12:00–15:00 около базы. '
               'В последний день выезд из номера перед прогулкой, багаж у путешественника.')
    if submitted:
        try:
            if not selected_cities or not sum(interest_values):
                raise ValueError('Выберите направления и задайте ненулевые интересы')
            ahp_result = analyze_ahp(matrix) if weight_method.startswith('AHP') else None
            chosen_weights = ahp_result.as_weights() if ahp_result else Weights.from_points(**dict(zip(Weights.model_fields,weight_values)))
            if ahp_result:
                st.info(f'AHP: CR={ahp_result.consistency_ratio:.4f}; согласованность приемлема. Веса вычислены из сравнений.')
            request = TripRequest(departure=datetime.combine(window.start,time(8),tzinfo=ZoneInfo('Europe/Moscow')),
                return_by=datetime.combine(window.end,time(22),tzinfo=ZoneInfo('Europe/Moscow')),
                budget_rub=budget,interests=Distribution(**{k:v/sum(interest_values) for k,v in zip(Distribution.model_fields,interest_values)}),
                weights=chosen_weights,weight_method='ahp' if ahp_result else 'swing',
                ahp_matrix=matrix if ahp_result else None,max_walk_km_day=walking,
                active_minutes_day=active,min_success_rate=risk/100,temperature_min_c=temp[0],temperature_max_c=temp[1],
                max_rain_mm=rain,max_wind_kmh=wind,required_poi_ids=[p.id for p in required],excluded_poi_ids=[p.id for p in excluded])
            bar = st.progress(0,text='Проверяем направления и ограничения')
            def update(event):
                bar.progress(event['completed']/event['total'],text=f'{event["city"]}: проверено {event["completed"]}/{event["total"]} комплектов')
            with st.spinner('Составляем и проверяем маршруты…'):
                result = recommend(request,[c.id for c in selected_cities],settings=SearchSettings(backend='pulp' if backend_label.startswith('PuLP') else 'cp_sat'),allow_demo=True,progress=update)
            st.session_state['result'] = result
            bar.empty()
        except ValueError as error:
            st.session_state.pop("result",None)
            st.error(str(error))
    if 'result' in st.session_state:
        render_result(st.session_state['result'])


if __name__ == '__main__':
    main()
