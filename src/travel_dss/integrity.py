"""Dependency-free release integrity checks."""
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import List


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_release(root: Path) -> List[str]:
    path = root/'RELEASE_MANIFEST.json'
    if not path.is_file():
        return ['RELEASE_MANIFEST.json отсутствует: это не упакованный снимок релиза']
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or not isinstance(manifest.get('files'),dict):
        raise ValueError('Неизвестный формат манифеста релиза')
    issues = []
    for name,expected in manifest['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('Манифест содержит небезопасный путь')
        file = root.joinpath(*relative.parts)
        if file.is_symlink() or not file.is_file():
            issues.append(f'Нет обычного файла: {name}')
        elif file.stat().st_size != expected['bytes'] or sha256_file(file) != expected['sha256']:
            issues.append(f'Контрольная сумма не совпала: {name}')
    return issues
