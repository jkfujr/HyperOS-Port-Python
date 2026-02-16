import pytest
from src.modules.powerkeeper import PowerKeeperModule

@pytest.fixture
def powerkeeper_module(base_fixtures):
    return PowerKeeperModule(base_fixtures["smali"], base_fixtures["ctx"])

def test_powerkeeper_unlock_ftp(powerkeeper_module, base_fixtures, tmp_path):
    """Test PowerKeeper FTP unlock logic."""
    # Act
    powerkeeper_module.run(tmp_path)
    
    # Assert
    smali_mock = base_fixtures["smali"]
    calls = smali_mock.call_args_list
    
    # Expect 2 patches
    assert len(calls) == 2
    
    # 1. DisplayFrameSetting
    args1, kwargs1 = calls[0]
    assert kwargs1['iname'] == "DisplayFrameSetting.smali"
    assert "setScreenEffect" in kwargs1['method']
    assert "return-void" in kwargs1['remake']
    
    # 2. ThermalManager
    args2, kwargs2 = calls[1]
    assert kwargs2['iname'] == "ThermalManager.smali"
    assert kwargs2['method'] == "getDisplayCtrlCode"
    assert "return v0" in kwargs2['remake']
