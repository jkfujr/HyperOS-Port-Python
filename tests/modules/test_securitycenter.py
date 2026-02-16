import pytest
from unittest.mock import call, ANY
from pathlib import Path
from src.modules.securitycenter import SecurityCenterModule

@pytest.fixture
def sec_module(base_fixtures):
    """Creates a SecurityCenterModule instance with mocked dependencies."""
    return SecurityCenterModule(base_fixtures["smali"], base_fixtures["ctx"])

def test_soh_patch_no_file(sec_module, base_fixtures, tmp_path):
    """Test SOH patch aborts gracefully if file not found."""
    # Run on empty tmp_path
    sec_module._patch_battery_health(tmp_path)
    
    # Assert no smali patch happened
    base_fixtures["smali"].assert_not_called()

def test_soh_patch_success(sec_module, base_fixtures, tmp_path):
    """Test SOH patch logic when file and field are found."""
    # Create fake smali file
    smali_file = tmp_path / "com/miui/powercenter/nightcharge/ChargeProtectFragment$d.smali"
    smali_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Content with required handleMessage and WeakReference field
    content = """
    .class public Lcom/miui/powercenter/nightcharge/ChargeProtectFragment$d;
    .field private myWeakRef:Ljava/lang/ref/WeakReference;
    
    .method public handleMessage(Landroid/os/Message;)V
        .locals 1
        return-void
    .end method
    """
    smali_file.write_text(content, encoding='utf-8')
    
    # Act
    sec_module._patch_battery_health(tmp_path)
    
    # Assert
    # Expect 2 calls: one for seek_keyword="battery_health_soh" (Writer), one for handleMessage (Reader)
    smali_mock = base_fixtures["smali"]
    assert smali_mock.call_count == 2
    
    # Verify Reader call (second call usually, but logic order matters)
    # The code calls Writer first (seek_keyword), then Reader (file_path)
    
    # Check 1st call (Writer)
    args1, kwargs1 = smali_mock.call_args_list[0]
    assert kwargs1.get('seek_keyword') == "battery_health_soh"
    assert "sys.hack.soh" in kwargs1.get('regex_replace')[1]
    
    # Check 2nd call (Reader)
    args2, kwargs2 = smali_mock.call_args_list[1]
    assert kwargs2.get('method') == "handleMessage"
    assert str(smali_file) == kwargs2.get('file_path')
    # Verify the field name 'myWeakRef' was correctly extracted and used in replacement code
    assert "myWeakRef" in kwargs2.get('regex_replace')[1]

def test_remove_intercept_timer(sec_module, base_fixtures, tmp_path):
    """Test removing intercept timer logic."""
    res_dir = tmp_path / "res" / "values"
    res_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Setup strings.xml
    (res_dir / "strings.xml").write_text('<resources><string name="timer_str">确定（%d）</string></resources>')
    
    # 2. Mock XmlUtils.get_id to return a known ID
    base_fixtures["xml"].get_id.return_value = "0x7f123456"
    
    # 3. Setup target smali file
    smali_file = tmp_path / "smali/Target.smali"
    smali_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Content must contain ID and invoke-virtual {p0} ... ()I before it
    content = """
    .method public initData()V
        .locals 4
        
        # Call to be patched
        invoke-virtual {p0}, Lcom/example/Target;->getTimer()I
        move-result v0
        
        # Usage of ID
        const v1, 0x7f123456
        invoke-virtual {p0, v1}, Landroid/content/Context;->getString(I)Ljava/lang/String;
        
        return-void
    .end method
    """
    smali_file.write_text(content)
    
    # Act
    sec_module._remove_intercept_timer(tmp_path)
    
    # Assert
    base_fixtures["smali"].assert_called_once()
    args, kwargs = base_fixtures["smali"].call_args
    
    assert kwargs['method'] == "getTimer"
    assert kwargs['return_type'] == "I"
    assert kwargs['remake'] == ".locals 1\n    const/4 v0, 0x0\n    return v0"
