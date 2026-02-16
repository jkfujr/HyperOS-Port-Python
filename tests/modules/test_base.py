import pytest
from pathlib import Path
from src.modules.base import BaseModule

def test_base_module_structure(base_fixtures):
    """Test BaseModule base functionality."""
    
    # Create concrete implementation for testing
    class TestModule(BaseModule):
        def run(self, work_dir: Path):
            self.smali_patch(work_dir, test_arg="value")

    module = TestModule(base_fixtures["smali"], base_fixtures["ctx"])
    
    # Test logger init
    assert module.logger.name == "TestModule"
    
    # Test run calling smali_patch
    module.run(Path("/tmp"))
    base_fixtures["smali"].assert_called_with(path="/tmp", test_arg="value")

def test_base_module_abstract_error(base_fixtures):
    """Test NotImplementedError is raised if run is not implemented."""
    module = BaseModule(base_fixtures["smali"], base_fixtures["ctx"])
    
    with pytest.raises(NotImplementedError):
        module.run(Path("."))
