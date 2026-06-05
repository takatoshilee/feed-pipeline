import asyncio

from .config import load_config
from .pipeline import run


def main():
    config = load_config()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
