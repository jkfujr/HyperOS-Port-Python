"""EU Localization plugin.

This plugin applies EU localization bundle to the target ROM.
"""

import shutil
import tempfile
import zipfile
from pathlib import Path

from src.core.modifiers.plugin_system import ModifierPlugin, ModifierRegistry


@ModifierRegistry.register
class EULocalizationPlugin(ModifierPlugin):
    """Plugin to apply EU localization bundle."""

    name = "eu_localization"
    description = "Apply EU localization bundle to target ROM"
    priority = 50
    dependencies = ["wild_boost"]  # Run after wild_boost

    def check_prerequisites(self) -> bool:
        """Check if EU bundle is available."""
        # If user provides --eu-bundle, they explicitly want to use this plugin.
        # We should not rely on 'is_port_eu_rom' flag which might be missing.
        return getattr(self.ctx, "eu_bundle", None) is not None

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
                with zipfile.ZipFile(bundle_path, "r") as z:
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
        self.logger.info("Scanning EU bundle for APKs to replace...")

        # 1. Identify all unique packages in the bundle
        bundle_packages = {}  # pkg_name -> list of paths
        for apk_file in bundle_path.rglob("*.apk"):
            pkg_name = self.ctx.syncer._get_apk_package_name(apk_file)
            if pkg_name:
                if pkg_name not in bundle_packages:
                    bundle_packages[pkg_name] = []
                bundle_packages[pkg_name].append(apk_file)

        self.logger.info(f"Found {len(bundle_packages)} unique package(s) in EU Bundle.")

        # 2. For each unique package, find and remove original app in target ROM
        for pkg_name in bundle_packages:
            # Search for matching app in target ROM (global search)
            # Ensure cache is built by calling find_apks_by_package
            target_apks = self.ctx.syncer.find_apks_by_package(pkg_name, self.ctx.target_dir)

            if target_apks:
                # Get versions for comparison
                eu_apk_path = bundle_packages[pkg_name][0]
                eu_version = "Unknown"
                try:
                    eu_version = self.ctx.syncer._get_apk_version(eu_apk_path) or "Unknown"
                except Exception:
                    pass

                target_version = "Unknown"
                if target_apks[0].exists():
                    try:
                        target_version = (
                            self.ctx.syncer._get_apk_version(target_apks[0]) or "Unknown"
                        )
                    except Exception:
                        pass

                self.logger.info(
                    f"Replacing EU App: {pkg_name} | Target: {target_version} -> EU: {eu_version} ({len(target_apks)} instance(s))"
                )

                for target_apk in target_apks:
                    if not target_apk.exists():
                        continue

                    app_dir = target_apk.parent
                    self.logger.info(f"  - Found at: {target_apk.relative_to(self.ctx.target_dir)}")

                    # Safety check: avoid deleting root partition dirs (app, priv-app, etc.)
                    protected_dirs = {
                        "app",
                        "priv-app",
                        "system",
                        "product",
                        "system_ext",
                        "vendor",
                        "overlay",
                        "framework",
                        "mi_ext",
                        "odm",
                        "oem",
                    }

                    if app_dir.name not in protected_dirs:
                        self.logger.debug(f"  - Removing directory: {app_dir}")
                        try:
                            shutil.rmtree(app_dir)
                        except Exception as e:
                            self.logger.error(f"  - Failed to remove {app_dir}: {e}")
                    else:
                        self.logger.debug(
                            f"  - Removing single file (protected parent): {target_apk}"
                        )
                        target_apk.unlink()
            else:
                self.logger.debug(f"Adding new EU App: {pkg_name} (no match in target)")
