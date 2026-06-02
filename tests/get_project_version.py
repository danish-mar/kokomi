import tomli
from pathlib import Path

with open(Path(__file__).parent / "../pyproject.toml"  , "rb") as f:
    data = tomli.load(f)
    print(data["project"]["version"])