from collections.abc import Iterator

import pytest

from seating_stub import RunningServer, StubSeating


@pytest.fixture
def seating_server() -> Iterator[RunningServer]:
    server = RunningServer(StubSeating())
    server.start()
    try:
        yield server
    finally:
        server.stop()
