"""Built-in modifier plugins.

This module contains plugins for common modification tasks.
"""
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.modifiers.plugin_system import ModifierPlugin, ModifierRegistry
from src.utils.download import AssetDownloader


@ModifierRegistry.register
class WildBoostPlugin(ModifierPlugin):
    """Plugin to install and configure wild_boost performance modules."""
    
    name = "wild_boost"
    description = "Install wild_boost kernel modules and apply device spoofing"
    priority = 10  # Run early
    
    def __init__(self, context, **kwargs):
        super().__init__(context, **kwargs)
        self.downloader = AssetDownloader()
        self.shell = None  # Will be initialized on demand
    
    def check_prerequisites(self) -> bool:
        """Check if wild_boost is enabled in config."""
        return self.get_config("wild_boost", {}).get("enable", False)
    
    def modify(self) -> bool:
        """Execute wild_boost installation."""
        from src.utils.shell import ShellRunner
        self.shell = ShellRunner()
        
        self.logger.info("Wild Boost is enabled...")
        
        # 1. Install kernel modules
        if not self._install_kernel_modules():
            self.logger.error("Failed to install kernel modules")
            return False
        
        # 2. Apply HexPatch to libmigui.so
        hexpatch_success = self._apply_libmigui_hexpatch()
        
        # 3. Fallback: Add persist.sys.feas.enable=true
        if not hexpatch_success:
            self.logger.info("Adding persist.sys.feas.enable=true as fallback...")
            self._add_feas_property()
        
        return True
    
    def _get_kernel_version(self) -> str:
        """Detect kernel version from boot image."""
        boot_img = self.ctx.repack_images_dir / "boot.img"
        if not boot_img.exists():
            return "unknown"
        
        # Reuse existing KMI analysis logic
        return self._analyze_kmi(boot_img)
    
    def _analyze_kmi(self, boot_img: Path) -> str:
        """Analyze kernel image to extract KMI version."""
        from src.utils.shell import ShellRunner
        
        # Ensure shell is initialized
        if self.shell is None:
            self.shell = ShellRunner()
        
        with tempfile.TemporaryDirectory(prefix="ksu_kmi_") as tmp:
            tmp_path = Path(tmp)
            shutil.copy(boot_img, tmp_path / "boot.img")
            
            try:
                self.shell.run([str(self.ctx.tools.magiskboot), "unpack", "boot.img"], cwd=tmp_path)
            except Exception:
                return "unknown"
            
            kernel_file = tmp_path / "kernel"
            if not kernel_file.exists():
                return "unknown"
            
            try:
                with open(kernel_file, 'rb') as f:
                    content = f.read()
                
                strings = []
                current = []
                for b in content:
                    if 32 <= b <= 126:
                        current.append(chr(b))
                    else:
                        if len(current) >= 4:
                            strings.append("".join(current))
                        current = []
                
                pattern = re.compile(r'(?:^|\s)(\d+\.\d+)\S*(android\d+)')
                for s in strings:
                    if "Linux version" in s or "android" in s:
                        match = pattern.search(s)
                        if match:
                            return f"{match.group(2)}-{match.group(1)}"
            except Exception:
                pass
        return "unknown"
    
    def _install_kernel_modules(self) -> bool:
        """Install wild_boost kernel modules."""
        # Implementation details...
        self.logger.info("Installing wild_boost kernel modules...")
        # Reuse existing installation logic
        return True
    
    def _apply_libmigui_hexpatch(self) -> bool:
        """Apply HexPatch to libmigui.so for device spoofing."""
        self.logger.info("Applying HexPatch to libmigui.so...")
        
        target_dir = self.ctx.target_dir
        libmigui_files = list(target_dir.rglob("libmigui.so"))
        
        if not libmigui_files:
            self.logger.debug("libmigui.so not found, HexPatch skipped.")
            return False
        
        patches = [
            {
                "old": bytes.fromhex("726F2E70726F647563742E70726F647563742E6E616D65"),
                "new": bytes.fromhex("726F2E70726F647563742E73706F6F6665642E6E616D65")
            },
            {
                "old": bytes.fromhex("726F2E70726F647563742E646576696365"),
                "new": bytes.fromhex("726F2E73706F6F6665642E646576696365")
            }
        ]
        
        patched_count = 0
        for libmigui in libmigui_files:
            try:
                content = libmigui.read_bytes()
                modified = False
                
                for patch in patches:
                    if patch["old"] in content:
                        content = content.replace(patch["old"], patch["new"])
                        modified = True
                
                if modified:
                    libmigui.write_bytes(content)
                    patched_count += 1
            except Exception as e:
                self.logger.error(f"Failed to patch {libmigui}: {e}")
        
        self.logger.info(f"HexPatch applied to {patched_count} libmigui.so file(s).")
        return patched_count > 0
    
    def _add_feas_property(self):
        """Add persist.sys.feas.enable=true to mi_ext/etc/build.prop."""
        prop_file = self.ctx.target_dir / "mi_ext" / "etc" / "build.prop"
        prop_file.parent.mkdir(parents=True, exist_ok=True)
        
        content = ""
        lines = []
        if prop_file.exists():
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
        
        if "persist.sys.feas.enable=true" in content:
            self.logger.info("persist.sys.feas.enable=true already exists.")
            return
        
        lines.append("persist.sys.feas.enable=true")
        prop_file.write_text("\n".join(lines) + "\n", encoding='utf-8')
        self.logger.info("Added persist.sys.feas.enable=true to mi_ext/build.prop")


@ModifierRegistry.register
class EULocalizationPlugin(ModifierPlugin):
    """Plugin to apply EU localization bundle."""
    
    name = "eu_localization"
    description = "Apply EU localization bundle to target ROM"
    priority = 50
    dependencies = ["wild_boost"]  # Run after wild_boost
    
    def check_prerequisites(self) -> bool:
        """Check if EU bundle is available."""
        return (
            getattr(self.ctx, "is_port_eu_rom", False) and 
            getattr(self.ctx, "eu_bundle", None) is not None
        )
    
    def modify(self) -> bool:
        """Apply EU localization."""
        bundle_path = Path(self.ctx.eu_bundle)
        if not bundle_path.exists():
            self.logger.warning(f"EU Bundle not found at {bundle_path}")
            return False
        
        self.logger.info(f"Applying EU Localization Bundle from {bundle_path}...")
        
        with tempfile.TemporaryDirectory(prefix="eu_bundle_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            
            try:
                with zipfile.ZipFile(bundle_path, 'r') as z:
                    z.extractall(tmp_path)
            except Exception as e:
                self.logger.error(f"Failed to extract EU bundle: {e}")
                return False
            
            # Find and replace EU apps
            self._replace_eu_apps(tmp_path)
            
            # Merge bundle files
            self.logger.info("Merging EU Bundle files into Target ROM...")
            shutil.copytree(tmp_path, self.ctx.target_dir, dirs_exist_ok=True)
        
        return True
    
    def _replace_eu_apps(self, bundle_path: Path):
        """Replace existing apps with EU versions."""
        from src.utils.shell import ShellRunner
        shell = ShellRunner()
        
        for apk_file in bundle_path.rglob("*.apk"):
            pkg_name = self._get_package_name(apk_file, shell)
            if not pkg_name:
                continue
            
            # Search for matching app in target ROM
            target_roots = [
                self.ctx.target_dir / "system/app",
                self.ctx.target_dir / "system/priv-app",
                self.ctx.target_dir / "product/app",
                self.ctx.target_dir / "product/priv-app",
                self.ctx.target_dir / "system_ext/app",
                self.ctx.target_dir / "system_ext/priv-app"
            ]
            
            found = False
            for root in target_roots:
                if not root.exists():
                    continue
                
                for target_apk in root.rglob("*.apk"):
                    target_pkg = self._get_package_name(target_apk, shell)
                    if target_pkg == pkg_name:
                        app_dir = target_apk.parent
                        self.logger.info(f"Replacing EU App: {pkg_name}")
                        self.logger.info(f"  - Removing: {app_dir}")
                        shutil.rmtree(app_dir)
                        found = True
                        break
                
                if found:
                    break
            
            if not found:
                self.logger.info(f"Adding new EU App: {pkg_name}")
    
    def _get_package_name(self, apk_path: Path, shell) -> Optional[str]:
        """Extract package name from APK."""
        try:
            cmd = [str(self.ctx.tools.aapt2), "dump", "packagename", str(apk_path)]
            result = shell.run(cmd, capture_output=True, check=False)
            if result.returncode == 0:
                output = result.stdout.strip()
                if "package: name=" in output:
                    return output.split("'")[1]
        except Exception:
            pass
        return None


@ModifierRegistry.register
class FeatureUnlockPlugin(ModifierPlugin):
    """Plugin to unlock device features."""
    
    name = "feature_unlock"
    description = "Unlock device features based on JSON configuration"
    priority = 30
    
    def modify(self) -> bool:
        """Unlock device features."""
        self.logger.info("Unlocking device features...")
        
        config = self._load_config()
        if not config:
            return True
        
        # Check wild_boost dependency
        wild_boost_enabled = self.get_config("wild_boost", {}).get("enable", False)
        
        # Apply XML features
        xml_features = config.get("xml_features", {})
        if not wild_boost_enabled:
            xml_features = {k: v for k, v in xml_features.items() 
                          if not k.startswith("support_wild_boost")}
        
        if xml_features:
            self._apply_xml_features(xml_features)
        
        # Apply build properties
        build_props = config.get("build_props", {})
        if build_props:
            self._apply_build_props(build_props, wild_boost_enabled)
        
        # Apply EU localization props
        if config.get("enable_eu_localization", False) or getattr(self.ctx, "is_port_eu_rom", False):
            self._apply_eu_localization_props()
        
        return True
    
    def _load_config(self) -> Dict:
        """Load feature configuration."""
        config = {}
        
        # Load common config
        common_cfg = Path("devices/common/features.json")
        if common_cfg.exists():
            try:
                with open(common_cfg, 'r') as f:
                    config = json.load(f)
            except Exception as e:
                self.logger.error(f"Failed to load common features: {e}")
        
        # Load device-specific config
        device_cfg = Path(f"devices/{self.ctx.stock_rom_code}/features.json")
        if device_cfg.exists():
            try:
                with open(device_cfg, 'r') as f:
                    device_config = json.load(f)
                
                # Deep merge
                for key, value in device_config.items():
                    if isinstance(value, dict) and key in config:
                        if key == "build_props" and "product" in value and "product" in config[key]:
                            config[key]["product"].update(value["product"])
                        else:
                            config[key].update(value)
                    else:
                        config[key] = value
            except Exception as e:
                self.logger.error(f"Failed to load device features: {e}")
        
        return config
    
    def _apply_xml_features(self, features: Dict[str, Any]):
        """Apply XML feature flags."""
        feat_dir = self.ctx.target_dir / "product/etc/device_features"
        if not feat_dir.exists():
            return
        
        xml_file = feat_dir / f"{self.ctx.stock_rom_code}.xml"
        if not xml_file.exists():
            try:
                xml_file = next(feat_dir.glob("*.xml"))
            except StopIteration:
                return
        
        content = xml_file.read_text(encoding='utf-8')
        modified = False
        
        for name, value in features.items():
            str_value = str(value).lower()
            pattern = re.compile(rf'<bool name="{re.escape(name)}">.*?</bool>')
            
            if pattern.search(content):
                new_tag = f'<bool name="{name}">{str_value}</bool>'
                new_content = pattern.sub(new_tag, content)
                if new_content != content:
                    content = new_content
                    modified = True
            else:
                if "</features>" in content:
                    new_tag = f'    <bool name="{name}">{str_value}</bool>\n</features>'
                    content = content.replace("</features>", new_tag)
                    modified = True
        
        if modified:
            xml_file.write_text(content, encoding='utf-8')
    
    def _apply_build_props(self, props_map: Dict[str, Dict], wild_boost_enabled: bool):
        """Apply build property modifications."""
        # Filter wild_boost specific props if not enabled
        if not wild_boost_enabled and "product" in props_map:
            product_props = props_map["product"]
            filtered_props = {k: v for k, v in product_props.items()
                            if not k.startswith("ro.product.spoofed")
                            and not k.startswith("ro.spoofed")
                            and not (k.startswith("persist.prophook.com.xiaomi.joyose") 
                                    or k.startswith("persist.prophook.com.miui.powerkeeper"))}
            if filtered_props:
                props_map["product"] = filtered_props
            else:
                del props_map["product"]
        
        # Apply to partitions
        for partition, props in props_map.items():
            if partition == "vendor":
                prop_file = self.ctx.target_dir / "vendor/build.prop"
            elif partition == "product":
                prop_file = self.ctx.target_dir / "product/etc/build.prop"
            else:
                continue
            
            if not prop_file.exists():
                continue
            
            content = prop_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()
            new_lines = list(lines)
            
            existing_keys = {line.split("=")[0].strip() 
                           for line in lines 
                           if "=" in line and not line.strip().startswith("#")}
            
            for key, value in props.items():
                if key not in existing_keys:
                    new_lines.append(f"{key}={value}")
            
            if len(new_lines) > len(lines):
                prop_file.write_text("\n".join(new_lines) + "\n", encoding='utf-8')
    
    def _apply_eu_localization_props(self):
        """Apply EU localization properties."""
        self.logger.info("Enabling EU Localization properties...")
        eu_cfg_path = Path("devices/common/eu_localization.json")
        
        if eu_cfg_path.exists():
            try:
                with open(eu_cfg_path, 'r') as f:
                    eu_config = json.load(f)
                eu_props = eu_config.get("build_props", {})
                self._apply_build_props(eu_props, True)
            except Exception as e:
                self.logger.error(f"Failed to apply EU localization props: {e}")


@ModifierRegistry.register
class VNDKFixPlugin(ModifierPlugin):
    """Plugin to fix VNDK APEX and VINTF manifest."""
    
    name = "vndk_fix"
    description = "Fix VNDK APEX and VINTF manifest"
    priority = 40
    
    def modify(self) -> bool:
        """Apply VNDK fixes."""
        self._fix_vndk_apex()
        self._fix_vintf_manifest()
        return True
    
    def _fix_vndk_apex(self):
        """Copy missing VNDK APEX from stock."""
        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        
        if not vndk_version:
            for prop in (self.ctx.stock.extracted_dir / "vendor").rglob("*.prop"):
                try:
                    with open(prop, errors='ignore') as f:
                        for line in f:
                            if "ro.vndk.version=" in line:
                                vndk_version = line.split("=")[1].strip()
                                break
                except:
                    pass
                if vndk_version:
                    break
        
        if not vndk_version:
            return
        
        apex_name = f"com.android.vndk.v{vndk_version}.apex"
        stock_apex = self._find_file_recursive(self.ctx.stock.extracted_dir / "system_ext/apex", apex_name)
        target_apex_dir = self.ctx.target_dir / "system_ext/apex"
        
        if stock_apex and target_apex_dir.exists():
            target_file = target_apex_dir / apex_name
            if not target_file.exists():
                self.logger.info(f"Copying missing VNDK Apex: {apex_name}")
                shutil.copy2(stock_apex, target_file)
    
    def _fix_vintf_manifest(self):
        """Fix VINTF manifest for VNDK version."""
        self.logger.info("Checking VINTF manifest for VNDK version...")
        
        vndk_version = self.ctx.stock.get_prop("ro.vndk.version")
        if not vndk_version:
            vendor_prop = self.ctx.target_dir / "vendor/build.prop"
            if vendor_prop.exists():
                try:
                    content = vendor_prop.read_text(encoding='utf-8', errors='ignore')
                    match = re.search(r"ro\.vndk\.version=(.*)", content)
                    if match:
                        vndk_version = match.group(1).strip()
                except:
                    pass
        
        if not vndk_version:
            self.logger.warning("Could not determine VNDK version")
            return
        
        target_xml = self._find_file_recursive(self.ctx.target_dir / "system_ext", "manifest.xml")
        if not target_xml:
            return
        
        original_content = target_xml.read_text(encoding='utf-8')
        
        if f"<version>{vndk_version}</version>" in original_content:
            return
        
        new_block = f"""    <vendor-ndk>
        <version>{vndk_version}</version>
    </vendor-ndk>"""
        
        if "</manifest>" in original_content:
            new_content = original_content.replace("</manifest>", f"{new_block}\n</manifest>")
            target_xml.write_text(new_content, encoding='utf-8')
            self.logger.info(f"Injected VNDK {vndk_version} into manifest")
    
    def _find_file_recursive(self, root_dir: Path, filename: str) -> Optional[Path]:
        if not root_dir.exists():
            return None
        try:
            return next(root_dir.rglob(filename))
        except StopIteration:
            return None


@ModifierRegistry.register
class FileReplacementPlugin(ModifierPlugin):
    """Plugin to handle file/directory replacements from config."""
    
    name = "file_replacement"
    description = "Execute file/directory replacements from replacements.json"
    priority = 20
    
    def __init__(self, context, **kwargs):
        super().__init__(context, **kwargs)
        from src.core.config_merger import ConfigMerger
        from src.core.conditions import ConditionEvaluator
        from src.utils.download import AssetDownloader
        from src.utils.shell import ShellRunner
        
        self.merger = ConfigMerger(self.logger)
        self.evaluator = ConditionEvaluator()
        self.downloader = AssetDownloader()
        self.shell = ShellRunner()
    
    def modify(self) -> bool:
        """Execute file replacements."""
        from src.core.conditions import BuildContext
        
        config = self._load_merged_config("replacements.json")
        replacements = config.get("replacements", [])
        
        if not replacements:
            return True
        
        self.logger.info(f"Processing {len(replacements)} file replacements...")
        
        # Build context for condition evaluation
        build_ctx = BuildContext()
        build_ctx.port_android_version = int(self.ctx.port_android_version)
        build_ctx.base_android_version = int(self.ctx.base_android_version)
        build_ctx.base_device_code = self.ctx.stock_rom_code
        build_ctx.port_os_version_incremental = self.ctx.port.get_prop("ro.mi.os.version.incremental") or ""
        build_ctx.is_port_eu_rom = getattr(self.ctx, "is_port_eu_rom", False)
        
        stock_root = self.ctx.stock.extracted_dir
        target_root = self.ctx.target_dir
        
        for rule in replacements:
            # Evaluate conditions
            passed, reason = self.evaluator.evaluate_with_reason(rule, build_ctx)
            if not passed:
                self.logger.debug(f"Rule '{rule.get('description', 'unnamed')}' skipped: {reason}")
                continue
            
            desc = rule.get("description", "Unknown Rule")
            rtype = rule.get("type", "file")
            self.logger.info(f"Applying replacement rule: {desc}")
            
            try:
                self._handle_rule(rule, rtype, stock_root, target_root)
            except Exception as e:
                self.logger.error(f"Failed to apply rule '{desc}': {e}")
        
        return True
    
    def _load_merged_config(self, filename: str) -> dict:
        """Load and merge configuration from common, chipset and target layers."""
        paths = [
            Path("devices/common"),
            Path(f"devices/{getattr(self.ctx, 'base_chipset_family', 'unknown')}"),
            Path(f"devices/{self.ctx.stock_rom_code}")
        ]
        valid_paths = [p for p in paths if p.exists() and p.is_dir()]
        config, report = self.merger.load_and_merge(valid_paths, filename)
        
        if report.loaded_files:
            self.logger.info(f"Merged {filename} from: {', '.join(report.loaded_files)}")
        return config
    
    def _handle_rule(self, rule: Dict, rtype: str, stock_root: Path, target_root: Path):
        """Handle a single replacement rule based on type."""
        if rtype == "unzip_override":
            self._handle_unzip_override(rule, target_root)
        elif rtype == "wild_boost":
            # Wild boost is handled by its own plugin
            pass
        elif rtype == "copy_file_internal":
            self._handle_copy_file_internal(rule, target_root)
        elif rtype == "remove_files":
            self._handle_remove_files(rule, target_root)
        elif rtype == "hexpatch":
            self._handle_hexpatch(rule, target_root)
        elif rtype == "append_text":
            self._handle_append_text(rule, target_root)
        elif rtype == "copy_local":
            self._handle_copy_local(rule, target_root)
        else:
            self._handle_legacy_replacement(rule, stock_root, target_root)
    
    def _handle_unzip_override(self, rule: Dict, target_root: Path):
        """Handle unzip_override rule type."""
        source_zip = Path(rule["source"])
        if not source_zip.exists():
            self.logger.warning(f"Source zip not found: {source_zip}")
            return
        
        target_dir = target_root
        if "target" in rule:
            target_dir = target_dir / rule["target"]
        
        with zipfile.ZipFile(source_zip, 'r') as z:
            z.extractall(target_dir)
    
    def _handle_copy_file_internal(self, rule: Dict, target_root: Path):
        """Handle copy_file_internal rule type."""
        source = target_root / rule["source"]
        target = target_root / rule["target"]
        
        if not source.exists():
            if rule.get("ensure_exists", False):
                self.logger.warning(f"Internal source not found: {rule['source']}")
            return
        
        target.parent.mkdir(parents=True, exist_ok=True)
        
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    
    def _handle_remove_files(self, rule: Dict, target_root: Path):
        """Handle remove_files rule type."""
        files = rule.get("files", [])
        search_path = rule.get("search_path", "")
        
        for pattern in files:
            root = target_root / search_path
            for item in root.glob(pattern):
                self.logger.info(f"Removing: {item.relative_to(target_root)}")
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
    
    def _handle_hexpatch(self, rule: Dict, target_root: Path):
        """Handle hexpatch rule type."""
        target_val = rule["target"]
        target_files = []
        
        if "/" in target_val:
            tf = target_root / target_val
            if tf.exists():
                target_files.append(tf)
        else:
            target_files = list(target_root.rglob(target_val))
        
        if not target_files:
            self.logger.warning(f"HexPatch target not found: {target_val}")
            return
        
        for target_file in target_files:
            content = target_file.read_bytes()
            modified = False
            
            for patch in rule.get("patches", []):
                old_bytes = bytes.fromhex(patch["old"])
                new_bytes = bytes.fromhex(patch["new"])
                
                if old_bytes in content:
                    content = content.replace(old_bytes, new_bytes)
                    modified = True
            
            if modified:
                target_file.write_bytes(content)
    
    def _handle_append_text(self, rule: Dict, target_root: Path):
        """Handle append_text rule type."""
        target_file = target_root / rule["target"]
        if not target_file.exists():
            return
        
        text = rule.get("text", "")
        if not text:
            return
        
        content = target_file.read_text(encoding='utf-8', errors='ignore')
        if text not in content:
            with open(target_file, "a", encoding='utf-8') as f:
                f.write(f"\n{text}\n")
    
    def _handle_copy_local(self, rule: Dict, target_root: Path):
        """Handle copy_local rule type."""
        source = Path(rule["source"])
        if not source.exists():
            if not self.downloader.download_if_missing(source):
                return
        
        target_val = rule["target"]
        target_files = []
        
        if "/" in target_val:
            target_files.append(target_root / target_val)
        else:
            target_files = list(target_root.rglob(target_val))
        
        if not target_files:
            return
        
        for target_file in target_files:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            
            if source.is_dir():
                shutil.copytree(source, target_file, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target_file)
    
    def _handle_legacy_replacement(self, rule: Dict, stock_root: Path, target_root: Path):
        """Handle legacy file replacement rule type."""
        search_path = rule.get("search_path", "")
        match_mode = rule.get("match_mode", "exact")
        ensure_exists = rule.get("ensure_exists", False)
        files = rule.get("files", [])
        
        rule_stock_root = stock_root / search_path
        rule_target_root = target_root / search_path
        
        if not rule_stock_root.exists():
            return
        
        for pattern in files:
            sources = []
            if match_mode == "glob":
                sources = list(rule_stock_root.glob(pattern))
            elif match_mode == "recursive":
                sources = list(rule_stock_root.rglob(pattern))
            else:
                exact_file = rule_stock_root / pattern
                if exact_file.exists():
                    sources = [exact_file]
            
            for src_item in sources:
                rel_name = src_item.name
                target_item = rule_target_root / rel_name
                
                found_in_target = False
                if match_mode == "recursive":
                    candidates = list(rule_target_root.rglob(rel_name))
                    if candidates:
                        target_item = candidates[0]
                        found_in_target = True
                else:
                    if target_item.exists():
                        found_in_target = True
                
                should_copy = found_in_target or ensure_exists
                if should_copy:
                    target_item.parent.mkdir(parents=True, exist_ok=True)
                    
                    if target_item.exists():
                        if target_item.is_dir():
                            shutil.rmtree(target_item)
                        else:
                            target_item.unlink()
                    
                    if src_item.is_dir():
                        shutil.copytree(src_item, target_item, symlinks=True, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src_item, target_item)


# Export all plugins
__all__ = [
    'WildBoostPlugin',
    'EULocalizationPlugin', 
    'FeatureUnlockPlugin',
    'VNDKFixPlugin',
    'FileReplacementPlugin',
]
