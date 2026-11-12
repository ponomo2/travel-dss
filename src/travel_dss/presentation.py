import html
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .recommendation import RecommendationResult


LABELS = {'transport':'Дорога туда-обратно','stay':'Все ночи проживания','food':'Питание',
          'transfers':'Два трансфера','admissions':'Входные билеты'}


def itinerary_rows(plan):
    return [{'День':day.day.isoformat(),'Начало':v.start.strftime('%H:%M'),'Конец':v.end.strftime('%H:%M'),
             'Место':v.name,'Переход, мин':v.travel_before_minutes,'Пешком до места, м':v.walk_before_m}
            for day in plan.days for v in day.visits]


def export_html(result: RecommendationResult) -> str:
    parts = ['<!doctype html><html lang="ru"><meta charset="utf-8"><title>План путешествия</title>',
             '<style>body{font:16px system-ui;max-width:1050px;margin:40px auto;padding:0 24px}table{border-collapse:collapse;width:100%;margin:20px 0}td,th{padding:8px;border:1px solid #ccd}h1,h2{color:#153d42}</style>',
             '<h1>Варианты путешествия</h1>',f'<p>{html.escape(result.metadata["notice"])}</p>',
             '<p>Местное время каждого направления. Стоимость в рублях.</p>']
    parts.append('<p>Предпочтения: '+html.escape(result.request.weight_method)+
                 '; веса '+html.escape(json.dumps(result.request.weights.model_dump(),ensure_ascii=False))+'</p>')
    for plan in result.plans:
        parts += [f'<h2>{html.escape(plan.destination_name)} · {plan.score*100:.1f}/100</h2>',
            f'<p>Типичная смета {plan.nominal_cost_rub:,.0f} ₽; C90 {plan.risk["cost_c90_rub"]:,.0f} ₽. '
            f'Успех сценариев {plan.risk["success_rate"]:.1%}; интервал '
            f'{plan.risk["wilson_lower"]:.1%}–{plan.risk["wilson_upper"]:.1%}. Статус {plan.solver.status}.</p>',
            '<table><tr><th>Статья</th><th>Рубли</th></tr>']
        parts += [f'<tr><td>{LABELS[k]}</td><td>{value:,.0f}</td></tr>' for k,value in plan.cost_breakdown.items()]
        parts += ['</table><table><tr><th>Дата</th><th>С</th><th>До</th><th>Объект</th><th>Переход</th></tr>']
        for row in itinerary_rows(plan):
            parts.append('<tr>'+''.join(f'<td>{html.escape(str(row[k]))}</td>' for k in ('День','Начало','Конец','Место','Переход, мин'))+'</tr>')
        parts.append('</table>')
        for day in plan.days:
            parts.append(f'<p>{day.day}: возврат к базе {day.end:%H:%M}, последняя прогулка '
                         f'{day.return_minutes} мин; всего {day.walk_m/1000:.1f} км.</p>')
        for stress in plan.stress_tests:
            parts.append(f'<p>Проверка закрытия: {html.escape(stress["status"])} — {html.escape(stress.get("explanation",""))}</p>')
        for step in plan.knowledge_trace.get('trace',[]):
            parts.append('<p>Вывод базы знаний: '+html.escape(step['explanation'])+'</p>')
        decision = plan.decision_analysis
        if decision.get('status') == 'evaluated':
            threshold = decision['break_even_probability']
            parts.append('<p>Подготовка резерва: '+html.escape(decision['interpretation'])+'</p>')
            if threshold is not None:
                parts.append(f'<p>Порог вероятности закрытия {threshold:.2%}; это анализ предположений, частота закрытий не оценена.</p>')
    for explanation in result.comparisons:
        parts.append(f'<p>Разница вариантов: {html.escape(explanation["text"])}</p>')
    parts.append('<details><summary>Метаданные расчета</summary><pre>'+html.escape(json.dumps(result.metadata,ensure_ascii=False,indent=2))+'</pre></details></html>')
    return '\n'.join(parts)


def save_feedback(database: Path, plan_id: str, decision: str, reason: str) -> None:
    if decision not in ('selected','rejected'):
        raise ValueError('Неизвестный тип обратной связи')
    database.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS feedback (created_at TEXT,plan_id TEXT,decision TEXT,reason TEXT)')
        connection.execute('INSERT INTO feedback VALUES (?,?,?,?)',
                           (datetime.now(timezone.utc).isoformat(),plan_id,decision,reason))
