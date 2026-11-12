"""Run chart queries through the real Superset REST API using a local test client."""
import json
from pathlib import Path

from superset.app import create_app

ROOT = Path(__file__).resolve().parents[1]
app = create_app()
with app.app_context():
    from superset.extensions import db
    from superset.models.dashboard import Dashboard

    client = app.test_client()
    credentials = json.loads((ROOT/'.runtime/superset/admin.json').read_text())
    login = client.post('/api/v1/security/login',json={**credentials,'provider':'db','refresh':False})
    assert login.status_code == 200,login.status_code
    headers = {'Authorization':'Bearer '+login.json['access_token']}
    csrf = client.get('/api/v1/security/csrf_token/',headers=headers)
    assert csrf.status_code == 200,csrf.status_code
    headers['X-CSRFToken'] = csrf.json['result']
    signed_in = client.post('/login/',data={**credentials,'csrf_token':csrf.json['result']})
    assert signed_in.status_code == 302,signed_in.status_code
    dashboard = db.session.query(Dashboard).filter_by(slug='travel-dss').one()
    rows = []
    for chart in sorted(dashboard.slices,key=lambda item:item.id):
        form = json.loads(chart.params)
        query = {'columns':form.get('all_columns',[]),'metrics':form.get('metrics',[]),
                 'row_limit':2500,'time_range':'No filter','filters':[],
                 'extras':{'where':'','having':''},'orderby':[]}
        if chart.viz_type == 'echarts_timeseries_line':
            query.update(granularity='date',is_timeseries=True,columns=[],
                         filters=[{'col':'city','op':'==','val':'yaroslavl'}],
                         extras={'time_grain_sqla':'P1D','where':'','having':''})
        from flask import g
        g.pop('user',None)
        g.pop('_login_user',None)
        response = client.post('/api/v1/chart/data',headers=headers,json={
            'datasource':{'id':chart.datasource_id,'type':'table'},'force':True,
            'queries':[query],'form_data':form,'result_format':'json','result_type':'full'})
        if response.status_code != 200:
            raise RuntimeError(f'{chart.slice_name}: {response.status_code}: {response.get_data(as_text=True)[:1000]}')
        result = response.json['result'][0]
        if result.get('status') == 'failed' or result.get('error'):
            raise RuntimeError(str(result))
        assert result['data'],chart.slice_name
        rows.append({'chart_id':chart.id,'chart':chart.slice_name,'http_status':response.status_code,
                     'rows':len(result['data']),'columns':result['colnames'],'status':result['status']})
    exported = client.get('/api/v1/dashboard/export/?q='+json.dumps([dashboard.id]),headers=headers)
    assert exported.status_code == 200,exported.get_data(as_text=True)[:1000]
    (ROOT/'.runtime/superset/dashboard-export.zip').write_bytes(exported.data)
    report = {'superset_version':'4.1.4','verification':'real Flask REST API, authenticated; not a mocked database query',
              'charts':rows,'dashboard_export_http_status':exported.status_code}
    (ROOT/'reports/passport/superset_queries.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
