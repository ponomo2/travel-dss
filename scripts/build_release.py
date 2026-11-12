"""Build a source release with an explicit allowlist and content manifest."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from travel_dss.integrity import sha256_file, verify_release


TOP_LEVEL = {'README.md','HANDOFF.md','IMPLEMENTATION_PLAN.md','Makefile','pyproject.toml',
             'requirements.lock','app.py','.gitignore','.python-version','THIRD_PARTY_NOTICES.md','Паспорт СППР.md'}
REPORTS = {'STAGES_1_4.md','STAGES_5_8.md','criteria_demo.json','criteria_sensitivity.png',
           'data_coverage.json','data_manifest.json','demo_catalog_selection.json','demo_forecast.txt',
           'demo_trip_data.json','full_demo.json','full_demo.html','system_smoke.json','validated_request.json','PASSPORT_AUDIT.md'}


def included(relative: Path) -> bool:
    parts = relative.parts
    if any(part in {'.venv','.superset-venv','.runtime','.git','__pycache__','.pytest_cache','.DS_Store'}
           or part.endswith('.egg-info') for part in parts):
        return False
    if relative.suffix in {'.pyc','.pyo','.sqlite','.sqlite3','.db','.log'}:
        return False
    if len(parts) == 1:
        return parts[0] in TOP_LEVEL
    if parts[0] in {'src','scripts','tests','docs','examples','data'}:
        return not any(part.startswith('.') for part in parts)
    if parts[0] == 'bi':
        return relative.suffix in {'.py','.lock'}
    if parts[:2] == ('artifacts','models'):
        return relative.suffix == '.json'
    if parts[0] == 'reports':
        if len(parts) == 2:
            return parts[1] in REPORTS
        return (parts[1] in {'forecast','experiments'} and relative.suffix in {'.md','.json','.csv','.png'}
                or parts[1] == 'passport' and relative.suffix in {'.md','.json','.html','.png','.xlsx','.lp','.mps'})
    return relative.as_posix() == '.streamlit/config.toml'


def build_release(destination: Path) -> Path:
    version = re.search(r'^version = "([^"]+)"',(ROOT/'pyproject.toml').read_text(),re.MULTILINE).group(1)
    folder = f'travel-dss-{version}'
    destination.mkdir(parents=True,exist_ok=True)
    archive = destination/f'{folder}.zip'
    files = []
    for current,directories,names in os.walk(ROOT,followlinks=False):
        directories[:] = [name for name in directories if not name.startswith('.') or name=='.streamlit']
        if Path(current) == ROOT:
            directories[:] = [name for name in directories if name in {'src','scripts','tests','docs','examples','data','bi','artifacts','reports','.streamlit'}]
        for name in names:
            path = Path(current)/name
            if path.is_file() and not path.is_symlink() and included(path.relative_to(ROOT)):
                files.append(path)
    files.sort()
    manifest = {'schema_version':1,'project':'travel-dss','version':version,
        'distribution':'source with bundled data and trained JSON models; editable installation required',
        'files':{p.relative_to(ROOT).as_posix():{'bytes':p.stat().st_size,'sha256':sha256_file(p)} for p in files}}
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for file in files:
            member = zipfile.ZipInfo(f'{folder}/{file.relative_to(ROOT).as_posix()}',date_time=(2026,11,12,0,0,0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            bundle.writestr(member,file.read_bytes())
        member = zipfile.ZipInfo(f'{folder}/RELEASE_MANIFEST.json',date_time=(2026,11,12,0,0,0))
        member.compress_type = zipfile.ZIP_DEFLATED
        member.external_attr = 0o100644 << 16
        bundle.writestr(member,json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    archive.with_suffix('.zip.sha256').write_text(f'{sha256_file(archive)}  {archive.name}\n')
    return archive


def main():
    parser = argparse.ArgumentParser(description='Сборка исходников для передачи разработчику')
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--verify',type=Path,help='Проверить распакованный релиз вместо сборки')
    args = parser.parse_args()
    if args.verify:
        issues = verify_release(args.verify.resolve())
        if issues:
            raise SystemExit('\n'.join(issues))
        print('Все файлы релиза соответствуют манифесту')
    else:
        archive = build_release(args.output.resolve())
        print(f'{archive}\nSHA256: {sha256_file(archive)}\nРазмер: {archive.stat().st_size/1024**2:.2f} MiB')


if __name__ == '__main__':
    main()
