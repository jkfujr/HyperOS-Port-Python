import logging
import os
from unittest.mock import MagicMock

import pytest

from src.core.context import PortingContext


@pytest.fixture(autouse=True, scope="session")
def force_source_language():
    """把日志语言固定为源码原文。

    测试里对 caplog 文本的断言基于源码中的英文原文，若不固定，结果会随
    环境中的 HYPEROS_LANG 漂移。
    """
    previous = os.environ.get("HYPEROS_LANG")
    os.environ["HYPEROS_LANG"] = "en"
    yield
    if previous is None:
        os.environ.pop("HYPEROS_LANG", None)
    else:
        os.environ["HYPEROS_LANG"] = previous


@pytest.fixture
def mock_context():
    """Returns a mock PortingContext."""
    mock = MagicMock(spec=PortingContext)
    mock.is_eu_port = False  # Default to CN port
    mock.logger = logging.getLogger("MockContext")
    return mock
