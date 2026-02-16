import pytest
from src.modules.joyose import JoyoseModule

@pytest.fixture
def joyose_module(base_fixtures):
    return JoyoseModule(base_fixtures["smali"], base_fixtures["ctx"])

def test_joyose_patches(joyose_module, base_fixtures, tmp_path):
    """Test Joyose cloud disable and GPU tuner enable."""
    # Act
    joyose_module.run(tmp_path)
    
    # Assert
    smali_mock = base_fixtures["smali"]
    calls = smali_mock.call_args_list
    
    assert len(calls) == 2
    
    # 1. Disable Cloud Sync
    args1, kwargs1 = calls[0]
    assert kwargs1['seek_keyword'] == "job exist, sync local..."
    assert "return-void" in kwargs1['remake']
    
    # 2. Enable GPU Tuner
    args2, kwargs2 = calls[1]
    assert kwargs2['seek_keyword'] == "GPUTUNER_SWITCH"
    assert kwargs2['return_type'] == "Z"
    assert "const/4 v0, 0x1" in kwargs2['remake']
