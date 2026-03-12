import logging
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

# Android ID mappings (from libcutils/fs_config.cpp)
AID_ROOT = "0"
AID_SYSTEM = "1000"
AID_RADIO = "1001"
AID_BLUETOOTH = "1002"
AID_SHELL = "2000"
AID_CACHE = "2001"
AID_DIAG = "2002"
AID_NET_BT_ADMIN = "3001"
AID_NET_BT = "3002"
AID_INET = "3003"
AID_NET_RAW = "3004"
AID_MISC = "9998"
AID_NOBODY = "9999"

# Capability bitmasks (from linux/capability.h)
CAP_SETGID = 0x20
CAP_SETUID = 0x40


class FsConfigGenerator:
    """Generates fs_config files for Android partitions."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("FsConfigGenerator")
        self.is_linux = sys.platform.startswith("linux")

    def generate(self, part_path: Path, output_path: Path) -> bool:
        """Generate fs_config file by scanning the directory.

        Args:
            part_path: Path to the extracted partition directory.
            output_path: Path where the fs_config file should be saved.

        Returns:
            True if successful, False otherwise.
        """
        if not part_path.exists():
            self.logger.error(f"Partition path not found: {part_path}")
            return False

        self.logger.info(f"Generating fs_config for {part_path.name}...")

        fs_config_lines = []

        try:
            for root, dirs, files in os.walk(part_path):
                # Handle directories
                for d in dirs:
                    full_path = Path(root) / d
                    rel_path = full_path.relative_to(part_path).as_posix()
                    uid, gid, mode, caps = self._get_attrs(full_path, rel_path, is_dir=True)
                    fs_config_lines.append(f"{rel_path} {uid} {gid} {mode} {caps}")

                # Handle files
                for f in files:
                    full_path = Path(root) / f
                    rel_path = full_path.relative_to(part_path).as_posix()
                    uid, gid, mode, caps = self._get_attrs(full_path, rel_path, is_dir=False)
                    fs_config_lines.append(f"{rel_path} {uid} {gid} {mode} {caps}")

            # Sort lines to ensure deterministic output
            fs_config_lines.sort()

            # Write to file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(fs_config_lines))
                f.write("\n")  # Ensure trailing newline

            self.logger.info(f"Generated fs_config at {output_path}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to generate fs_config: {e}")
            return False

    def _get_attrs(self, full_path: Path, rel_path: str, is_dir: bool) -> Tuple[str, str, str, str]:
        """Get UID, GID, Mode, and Capabilities.
        Prioritizes real filesystem attributes if on Linux and they seem valid (non-root owner).
        Otherwise falls back to Android-specific heuristic rules.
        """
        # Default fallback values
        uid = AID_ROOT
        gid = AID_ROOT
        caps = "0x0"
        mode = "0755" if is_dir else "0644"

        # 1. Try to get real attributes if on Linux
        if self.is_linux:
            try:
                st = os.stat(full_path)

                # If the file is owned by root (0), it might be default behavior of extraction,
                # but if it's NOT 0 (e.g. 1000 system), we definitely want to keep it.
                # However, if the user ran extract.erofs as non-root, all files might be owned by user.
                # So we check if st_uid is 0 or matches a known Android UID logic.
                # Actually, standard logic: if we have valid metadata, use it.
                # But 'extract.erofs' without root might map everything to current user.
                # We assume if st_uid != current_user_uid, it's valid.

                current_uid = os.getuid()
                if st.st_uid != current_uid:
                    uid = str(st.st_uid)
                    gid = str(st.st_gid)
                    # Mode: Keep only permission bits (0o777), mask out file type
                    mode = f"0{oct(st.st_mode & 0o7777)[2:]}"

                # Try to get capabilities
                # xattr key for caps is "security.capability"
                try:
                    # os.getxattr returns bytes
                    caps_raw = os.getxattr(full_path, "security.capability")
                    if caps_raw:
                        # TODO: Parse raw capability struct to hex string
                        # This is complex as it depends on VFS cap version (v2/v3).
                        # For now, if we can't easily parse, we might skip or implement a parser later.
                        # Using 0x0 is safer than writing garbage.
                        pass
                except (OSError, AttributeError):
                    pass

            except OSError:
                pass

        # 2. Apply Heuristic Rules (Override defaults if we didn't find specific UIDs)
        # Only apply heuristics if we are still at default root/root or if we want to enforce structure

        # We always enforce mode for well-known paths to ensure bootability
        if is_dir:
            mode = "0755"
        else:
            # Executables
            if "/bin/" in rel_path or "/xbin/" in rel_path:
                mode = "0755"
                if rel_path.endswith("/sh"):
                    gid = AID_SHELL
                elif rel_path.endswith("/run-as"):
                    mode = "0750"
                    gid = AID_SHELL
                    # CAP_SETUID | CAP_SETGID
                    # caps = "0xc0"

            elif rel_path.endswith(".sh"):
                mode = "0750"
                gid = AID_SHELL

            # Properties and Configs
            elif rel_path.endswith("build.prop"):
                mode = "0600"
            elif rel_path.endswith(".rc"):
                mode = "0644"
                uid = AID_ROOT
                gid = AID_ROOT

        return uid, gid, mode, caps


class ContextExtractor:
    """Extracts or finds SELinux file_contexts."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("ContextExtractor")

    def extract(self, part_path: Path, output_path: Path, part_name: str) -> bool:
        """Find and copy file_contexts.

        Args:
            part_path: Path to the extracted partition directory.
            output_path: Path where the file_contexts file should be saved.
            part_name: Name of the partition (e.g., 'system', 'vendor').

        Returns:
            True if found and copied, False otherwise.
        """
        self.logger.info(f"Searching for file_contexts for {part_name}...")

        candidates = []

        # 1. Search for exact name in root
        candidates.extend(part_path.glob(f"{part_name}_file_contexts"))

        # 2. Search in etc/selinux (Standard Android location)
        selinux_dir = part_path / "etc" / "selinux"
        if selinux_dir.exists():
            if part_name == "system":
                candidates.extend(selinux_dir.glob("plat_file_contexts"))
            elif part_name == "vendor":
                candidates.extend(selinux_dir.glob("vendor_file_contexts"))
            elif part_name == "product":
                candidates.extend(selinux_dir.glob("product_file_contexts"))
            elif part_name == "system_ext":
                candidates.extend(selinux_dir.glob("system_ext_file_contexts"))

            # Generic fallback in selinux dir
            candidates.extend(selinux_dir.glob("*_file_contexts"))

        # 3. Recursive search (Fallback)
        if not candidates:
            candidates.extend(part_path.rglob("*_file_contexts"))

        if candidates:
            source = candidates[0]
            self.logger.info(f"Found file_contexts: {source}")
            try:
                import shutil

                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output_path)
                return True
            except Exception as e:
                self.logger.error(f"Failed to copy file_contexts: {e}")
                return False
        else:
            self.logger.warning(
                f"No file_contexts found for {part_name}. Creating empty/default one."
            )
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w") as f:
                    f.write(f"# Default file_contexts for {part_name}\n")
                    f.write(f"/{part_name}(/.*)?  u:object_r:{part_name}_file:s0\n")
                return True
            except Exception as e:
                self.logger.error(f"Failed to create default file_contexts: {e}")
                return False
