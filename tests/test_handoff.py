import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from travel_dss.data import ROOT
from travel_dss.doctor import inspect_environment
from travel_dss.integrity import verify_release


def test_release_manifest_detects_changed_and_missing_files(tmp_path):
    file = tmp_path/'example.json'
    file.write_bytes(b'{}')
    manifest = {'schema_version':1,'files':{'example.json':{'bytes':2,'sha256':hashlib.sha256(b'{}').hexdigest()}}}
    (tmp_path/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest))
    assert not verify_release(tmp_path)
    file.write_bytes(b'[]')
    assert 'Контрольная сумма' in verify_release(tmp_path)[0]
    file.unlink()
    assert 'Нет обычного файла' in verify_release(tmp_path)[0]


def test_manifest_does_not_allow_parent_traversal(tmp_path):
    (tmp_path/'RELEASE_MANIFEST.json').write_text(json.dumps({'schema_version':1,'files':{'../secret':{}}}))
    with pytest.raises(ValueError,match='небезопасный'):
        verify_release(tmp_path)


def test_bundle_allowlist_excludes_environments_and_private_state():
    spec = importlib.util.spec_from_file_location('release_builder',ROOT/'scripts/build_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('.venv/bin/python','data/processed/feedback.sqlite','artifacts/recommendations/private.json',
                 'reports/my-trip.json','reports/test_results.txt','.streamlit/secrets.toml','.git/config',
                 '.superset-venv/bin/python','.runtime/superset/admin.json','.runtime/superset/secret-key'):
        assert not module.included(Path(name))
    for name in ('src/travel_dss/cli.py','data/api/demo_catalog.json','artifacts/models/kazan.json',
                 '.streamlit/config.toml','HANDOFF.md','Паспорт СППР.md','bi/requirements-superset.lock',
                 'reports/passport/AHP.xlsx','data/knowledge/travel.owl'):
        assert module.included(Path(name))


def test_doctor_checks_all_bundled_cities():
    report = inspect_environment()
    assert report['ok'] and report['demo_data_ready']
    assert len(report['checks']) == 6
    assert all(check['weather_rows'] == 4018 for check in report['checks'])
