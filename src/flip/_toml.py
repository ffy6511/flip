"""Unified TOML loader: tomllib (py311+) else tomli (py<311)."""

try:
    import tomllib as _tomllib  # py311+
    def load_toml(path):
        with open(path, "rb") as f:
            return _tomllib.load(f)
except ModuleNotFoundError:  # py<3.11
    import tomli as _tomli  # type: ignore
    def load_toml(path):
        with open(path, "rb") as f:
            return _tomli.load(f)


__all__ = ["load_toml"]
