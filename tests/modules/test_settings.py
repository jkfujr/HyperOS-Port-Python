import pytest
from unittest.mock import call, ANY
from src.modules.settings import SettingsModule

@pytest.fixture
def settings_module(base_fixtures):
    """Creates a SettingsModule instance with mocked dependencies."""
    return SettingsModule(base_fixtures["smali"], base_fixtures["ctx"])

def test_settings_cn_patch(settings_module, base_fixtures, tmp_path):
    """Test standard CN patching logic (is_eu_port=False)."""
    # Arrange
    base_fixtures["ctx"].is_eu_port = False
    
    # Act
    settings_module.run(tmp_path)
    
    # Assert
    smali_mock = base_fixtures["smali"]
    xml_mock = base_fixtures["xml"]
    
    # 1. Verify specific smali patches were called
    # Check for setupShowNotificationIconCount patch call
    calls = smali_mock.call_args_list
    
    # We expect 2 smali_patch calls for CN logic:
    # 1. Expand local register capacity
    # 2. Replace array instructions
    assert len(calls) == 2
    
    # Verify first call arguments
    args1, kwargs1 = calls[0]
    assert kwargs1['iname'] == "IconDisplayCustomizationSettings.smali"
    assert kwargs1['method'] == "setupShowNotificationIconCount"
    assert kwargs1['regex_replace'] == (r"\.locals\s+\d+", r".locals 7")
    
    # 2. Verify XML injections
    res_dir = tmp_path / "res"
    
    # Default Strings
    xml_mock.add_string.assert_any_call(res_dir, "display_notification_icon_5", "%d icons")
    xml_mock.add_string.assert_any_call(res_dir, "display_notification_icon_7", "%d icons")
    
    # Chinese Strings
    xml_mock.add_string.assert_any_call(res_dir, "display_notification_icon_5", "显示%d个", "zh-rCN")
    
    # Array Items
    xml_mock.add_array_item.assert_any_call(res_dir, 
        array_name="notification_icon_counts_entries", 
        items=["@string/display_notification_icon_5", "@string/display_notification_icon_7"]
    )
    xml_mock.add_array_item.assert_any_call(res_dir,
        array_name="notification_icon_counts_values",
        items=["5", "7"]
    )

def test_settings_eu_patch(settings_module, base_fixtures, tmp_path):
    """Test EU patching logic (is_eu_port=True)."""
    # Arrange
    base_fixtures["ctx"].is_eu_port = True
    
    # Act
    settings_module.run(tmp_path)
    
    # Assert
    smali_mock = base_fixtures["smali"]
    xml_mock = base_fixtures["xml"]
    
    # EU logic only calls smali_patch once for updateHeaderList
    assert smali_mock.call_count == 1
    
    args, kwargs = smali_mock.call_args
    assert kwargs['iname'] == "MiuiSettings.smali"
    assert kwargs['method'] == "updateHeaderList"
    # Check regex replacement logic
    assert "IS_GLOBAL_BUILD:Z" in kwargs['regex_replace'][0]
    
    # Verify NO XML operations were performed
    xml_mock.add_string.assert_not_called()
    xml_mock.add_array_item.assert_not_called()
