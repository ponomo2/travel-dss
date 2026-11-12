"""Build an actual Apache Superset dashboard from local data marts."""
import json
from pathlib import Path

from superset.app import create_app

ROOT = Path(__file__).resolve().parents[1]
app = create_app()
with app.app_context():
    from superset.extensions import db
    from superset.connectors.sqla.models import SqlaTable
    from superset.models.core import Database
    from superset.models.dashboard import Dashboard
    from superset.models.slice import Slice

    credentials = json.loads((ROOT/'.runtime/superset/admin.json').read_text())
    security = app.appbuilder.sm
    admin = security.find_user(username=credentials['username'])
    if admin is None:
        admin = security.add_user(credentials['username'],'Local','Reviewer','reviewer@example.invalid',
            security.find_role('Admin'),credentials['password'])
    database = db.session.query(Database).filter_by(database_name='Travel DSS').one_or_none()
    if database is None:
        database = Database(database_name='Travel DSS',sqlalchemy_uri='sqlite:///'+str(ROOT/'data/processed/bi.sqlite'))
        db.session.add(database)
        db.session.flush()
    tables = {}
    for name in ('recommendations','forecast_test','forecast_quality','visits'):
        table = db.session.query(SqlaTable).filter_by(table_name=name,database_id=database.id).one_or_none()
        if table is None:
            table = SqlaTable(table_name=name,database=database)
            db.session.add(table)
            db.session.flush()
        table.fetch_metadata()
        tables[name] = table
    definitions = [
        ('Направления: полезность, расходы и риск','recommendations','table',
         {'query_mode':'raw','all_columns':['city','score','nominal_cost_rub','cost_c90_rub','success_rate','solver_status']}),
        ('Качество прогноза на независимом 2025 году','forecast_quality','table',
         {'query_mode':'raw','all_columns':['city','mae_c','coverage_80','interval_width_c','model']}),
        ('Температура: факт и сезонный прогноз, Ярославль','forecast_test','echarts_timeseries_line',
         {'granularity_sqla':'date','x_axis':'date','time_grain_sqla':'P1D','groupby':[],
          'metrics':[{'expressionType':'SQL','sqlExpression':'AVG(temperature_actual_c)','label':'ERA5, факт'},
                     {'expressionType':'SQL','sqlExpression':'AVG(temperature_predicted_c)','label':'Сезонный прогноз'}],
          'adhoc_filters':[{'expressionType':'SIMPLE','subject':'city','operator':'==','comparator':'yaroslavl','clause':'WHERE'}],
          'show_legend':True,'rich_tooltip':True}),
        ('Расписание посещений','visits','table',
         {'query_mode':'raw','all_columns':['city','date','name','start','end','walk_before_m']})]
    charts = []
    for title,table_name,viz,params in definitions:
        chart = db.session.query(Slice).filter_by(slice_name=title).one_or_none()
        table = tables[table_name]
        if chart is None:
            chart = Slice(slice_name=title,owners=[admin])
            db.session.add(chart)
        chart.datasource_id,chart.datasource_type,chart.viz_type = table.id,'table',viz
        chart.params = json.dumps({'viz_type':viz,'datasource':f'{table.id}__table','time_range':'No filter',
            'row_limit':2500,'adhoc_filters':[],**params},ensure_ascii=False)
        charts.append(chart)
    db.session.flush()
    dashboard = db.session.query(Dashboard).filter_by(slug='travel-dss').one_or_none()
    if dashboard is None:
        dashboard = Dashboard(dashboard_title='СППР путешествий — аналитика',slug='travel-dss',owners=[admin])
        db.session.add(dashboard)
    dashboard.slices = charts
    dashboard.published = True
    position = {'DASHBOARD_VERSION_KEY':'v2','ROOT_ID':{'id':'ROOT_ID','type':'ROOT','children':['GRID_ID']},
        'GRID_ID':{'id':'GRID_ID','type':'GRID','parents':['ROOT_ID'],'children':[]}}
    for row,chart in enumerate(charts):
        row_id,chart_id = f'ROW-{row}',f'CHART-{chart.id}'
        position['GRID_ID']['children'].append(row_id)
        position[row_id] = {'id':row_id,'type':'ROW','parents':['ROOT_ID','GRID_ID'],'children':[chart_id],'meta':{'background':'BACKGROUND_TRANSPARENT'}}
        position[chart_id] = {'id':chart_id,'type':'CHART','parents':['ROOT_ID','GRID_ID',row_id],'children':[],
            'meta':{'chartId':chart.id,'width':12,'height':70 if row==2 else 45,'sliceName':chart.slice_name}}
    dashboard.position_json = json.dumps(position)
    dashboard.json_metadata = json.dumps({'refresh_frequency':0,'color_scheme':'supersetColors'})
    db.session.commit()
    report = {'dashboard_id':dashboard.id,'path':'/superset/dashboard/travel-dss/','charts':[
        {'id':chart.id,'title':chart.slice_name,'viz_type':chart.viz_type,'table':chart.datasource.table_name} for chart in charts]}
    (ROOT/'reports/passport/superset_dashboard.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('Dashboard:',report['path'])
