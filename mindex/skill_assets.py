import shutil
from pathlib import Path

from .paths import package_root


def available_skill_directories() -> list[Path]:
    skills_root = package_root() / 'assets' / 'skills'
    return sorted(path for path in skills_root.iterdir() if path.is_dir())


def install_skills(codex_home_path: Path) -> list[Path]:
    installed = []
    destination_root = codex_home_path / 'skills'
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in available_skill_directories():
        destination = destination_root / source.name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        installed.append(destination)
    return installed
