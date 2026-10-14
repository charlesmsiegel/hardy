"""Simulate creation-time ctime with a controlled, independent write generation."""
from pathlib import Path


def windows_creation_clock(monkeypatch):
    from hardy.literature import library
    from hardy.literature.sources import artifacts, representations

    original_stat = Path.lstat
    original_write = Path.write_bytes
    generations = {}

    class CreationStat:
        def __init__(self, held):
            self.held = held
            self.st_ctime_ns = 100

        def __getattr__(self, name):
            return getattr(self.held, name)

    def write(path, data):
        result = original_write(path, data)
        generations[path] = generations.get(path, 0) + 1
        return result

    monkeypatch.setattr(Path, 'lstat', lambda path: CreationStat(original_stat(path)))
    monkeypatch.setattr(Path, 'write_bytes', write)
    for module in (library, artifacts, representations):
        monkeypatch.setattr(module, 'change_token', lambda path, info: generations.get(path, 0))
