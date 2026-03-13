import argparse
import sys
import shutil
from pathlib import Path

# Add project root to path to allow imports from src
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from src.utils.shell import ShellRunner

def is_sparse(path):
    """Check if image is Android sparse format."""
    try:
        with open(path, 'rb') as f:
            magic = f.read(4)
            return magic == b'\x3a\xff\x26\xed'
    except Exception:
        return False

def normalize_path(p_str):
    """Normalize path string for Linux environment."""
    # Remove quotes
    p_str = p_str.strip('"').strip("'")
    
    # Replace backslashes with forward slashes
    p_str = p_str.replace('\\', '/')
    
    # Handle WSL UNC paths (e.g., //wsl$/Ubuntu/root/...)
    if "//wsl$/" in p_str or "/wsl$/" in p_str:
        parts = p_str.split('/')
        try:
            # Find where wsl$ is
            wsl_idx = -1
            for i, part in enumerate(parts):
                if part == "wsl$":
                    wsl_idx = i
                    break
            
            if wsl_idx != -1 and len(parts) > wsl_idx + 2:
                # path starts after DistroName (wsl_idx + 2)
                real_path_parts = parts[wsl_idx + 2:]
                p_str = "/" + "/".join(real_path_parts)
        except Exception:
            pass
            
    # If path starts with "root/" (e.g. root/Downloads/...), make it absolute /root/
    if p_str.startswith("root/"):
        p_str = "/" + p_str
             
    return p_str

def check_header(path):
    """Detect file type by magic header."""
    try:
        with open(path, 'rb') as f:
            header = f.read(1024)
            
            # Android Boot Image: "ANDROID!"
            if header.startswith(b"ANDROID!"):
                return "boot"
                
            # ELF (often used for kernel, modem, etc.): 0x7F 0x45 0x4C 0x46
            if header.startswith(b"\x7fELF"):
                return "elf"
            
            # DTB: 0xD00DFEED (Big Endian)
            if header.startswith(b"\xd0\x0d\xfe\xed"):
                return "dtb"

            # VBMETA: "AVB" signature usually at start or offset
            # VBMeta magic: 'AVB0' -> 0x41 0x56 0x42 0x30
            if header.startswith(b"AVB0"):
                return "vbmeta"
                
            # EROFS: 0xE2 0xE1 0xF5 0xE0 at offset 1024
            # We need to read more if offset 1024 is needed, but let's check basic first
            f.seek(1024)
            erofs_magic = f.read(4)
            if erofs_magic == b'\xe2\xe1\xf5\xe0':
                return "erofs"
                
    except Exception:
        pass
    return "unknown"

def extract_image(image_path_str, output_root, shell):
    image_path = Path(image_path_str)
    
    if not image_path.exists():
        print(f"[WARN] File not found: {image_path}")
        return

    print(f"Processing: {image_path.name}")
    
    # Determine output directory
    name = image_path.stem
    output_dir = output_root / name
    
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    # Handle sparse images
    work_image = image_path
    temp_raw = None
    
    if is_sparse(image_path):
        print(f"  - Detected sparse image, converting to raw...")
        temp_raw = output_dir / f"{name}.raw.img"
        try:
            # Try to find simg2img
            shell.run(["simg2img", str(image_path), str(temp_raw)])
            work_image = temp_raw
        except Exception as e:
            print(f"  - Failed to unsparse: {e}")
            # Continue trying with original image, maybe it wasn't sparse or tool missing
            pass

    # Detect file type
    file_type = check_header(work_image)
    print(f"  - Detected type: {file_type}")

    # Determine extraction method based on name/type
    # Boot images (kernel + ramdisk)
    boot_keywords = ["boot", "recovery", "dtbo", "dtb", "init_boot"]
    is_boot_name = any(k in name for k in boot_keywords) and "vendor" not in name and "system" not in name
    if "vendor_boot" in name: is_boot_name = True
    
    # Logic: 
    # 1. If header says BOOT -> Use Magiskboot
    # 2. If header says EROFS -> Use extract.erofs
    # 3. If name suggests BOOT but header is DTB/ELF -> Skip Magiskboot (it might crash), just copy or warn
    # 4. If name suggests FS but header unknown -> Try EROFS/7z
    
    if is_boot_name:
        if file_type == "elf":
            print(f"  - Identified as ELF binary (firmware/kernel) but has boot name. Copying directly...")
            shutil.copy2(work_image, output_dir / work_image.name)
        elif file_type == "dtb":
            print(f"  - Identified as raw DTB but has boot name. Copying directly...")
            shutil.copy2(work_image, output_dir / work_image.name)
        else:
            print(f"  - Extracting as Boot Image (using magiskboot)...")
            # Magiskboot unpacks to CWD
            try:
                # Use absolute path for input to avoid copying
                input_abs_path = work_image.resolve()
                # Note: magiskboot unpack <img_path> - unpacks to current directory
                # We must be in output_dir
                shell.run(["magiskboot", "unpack", str(input_abs_path)], cwd=output_dir)
                print(f"  - Extracted to: {output_dir}")
            except Exception as e:
                # Some boot images like qtvm_dtbo, dtbo might be raw DTBO sequences or custom formats
                # that magiskboot recognizes but fails to unpack correctly or crashes.
                # If it fails, fallback to copy.
                print(f"  - Magiskboot failed (code {getattr(e, 'returncode', '?')}): {e}")
                print(f"  - Fallback: Copying original image...")
                # Cleanup partial unpack if any
                if output_dir.exists():
                    for f in output_dir.iterdir():
                        if f.name != work_image.name: 
                            try:
                                if f.is_file(): f.unlink()
                                else: shutil.rmtree(f)
                            except: pass
                shutil.copy2(work_image, output_dir / work_image.name)
            
    elif file_type == "erofs":
        print(f"  - Extracting as EROFS Image...")
        try:
            input_abs_path = work_image.resolve()
            shell.run(["extract.erofs", "-x", "-i", str(input_abs_path), "-o", str(output_dir)])
            print(f"  - Extracted to: {output_dir}")
        except Exception as e:
            print(f"  - extract.erofs failed: {e}")
            
    elif file_type == "dtb":
        print(f"  - Identified as raw DTB file. Copying directly...")
        shutil.copy2(work_image, output_dir / work_image.name)
        
    elif file_type == "elf":
        print(f"  - Identified as ELF binary (firmware/kernel). Copying directly...")
        shutil.copy2(work_image, output_dir / work_image.name)
        
    elif file_type == "vbmeta":
        print(f"  - Identified as VBMeta image. Copying directly...")
        shutil.copy2(work_image, output_dir / work_image.name)
        
    else:
        # Fallback for Filesystem images (System, Vendor, etc.) or unknown firmware
        # Only try EROFS/7z if file size is substantial (>10MB) or name suggests partition
        fs_keywords = ["system", "vendor", "product", "odm", "mi_ext"]
        is_fs_name = any(k in name for k in fs_keywords)
        
        if is_fs_name:
            print(f"  - Extracting as Filesystem Image (trying extract.erofs)...")
            try:
                input_abs_path = work_image.resolve()
                shell.run(["extract.erofs", "-x", "-i", str(input_abs_path), "-o", str(output_dir)])
                print(f"  - Extracted to: {output_dir}")
            except Exception as e:
                print(f"  - extract.erofs failed.")
                # Optional: Try 7z as fallback for Ext4/other
                print("  - Trying 7z as fallback...")
                try:
                    shell.run(["7z", "x", str(work_image), f"-o{output_dir}"])
                    print(f"  - Extracted with 7z to: {output_dir}")
                except Exception as e2:
                    print(f"  - 7z failed or not found.")
        else:
            print(f"  - Unknown format and not a standard partition. Copying raw file...")
            if temp_raw and work_image == temp_raw:
                # If we converted it, keep the converted raw file but rename it to .img if needed
                # Actually, usually we want the original name.
                # If it was sparse, temp_raw is name.raw.img. 
                # Let's move it to final destination.
                target = output_dir / f"{name}.img"
                if target.exists(): target.unlink()
                shutil.move(temp_raw, target)
                temp_raw = None # Prevent cleanup
            else:
                shutil.copy2(work_image, output_dir / work_image.name)

    # Cleanup temp raw file
    if temp_raw and temp_raw.exists():
        temp_raw.unlink()

def pack_image(input_dir, output_image, shell):
    """Pack a directory into an image (EROFS/Boot)."""
    input_dir = Path(input_dir)
    output_image = Path(output_image)
    
    print(f"Processing Pack: {input_dir.name} -> {output_image.name}")
    
    if not input_dir.exists():
        print(f"[Error] Input directory not found: {input_dir}")
        return

    # Determine bin path based on OS
    bin_path = project_root / "bin"
    if sys.platform.startswith("linux"):
        bin_dir = bin_path / "linux" / "x86_64"
    elif sys.platform.startswith("win"):
        bin_dir = bin_path / "windows" # Assuming similar structure
    elif sys.platform.startswith("darwin"):
        bin_dir = bin_path / "macos"
    else:
        bin_dir = bin_path # Fallback

    # Helper to find tool
    def get_tool(name):
        # 1. Look in project bin
        tool = bin_dir / name
        if tool.exists(): return str(tool)
        # 2. Look in PATH
        return name

    # 1. Detect Type
    # Check for EROFS config files
    # The config files might be named like 'product_fs_config' or just 'fs_config'
    # We should search for them.
    
    config_dir = input_dir / "config"
    fs_config = None
    file_contexts = None
    
    if config_dir.exists():
        # Try exact match first
        if (config_dir / "fs_config").exists(): fs_config = config_dir / "fs_config"
        if (config_dir / "file_contexts").exists(): file_contexts = config_dir / "file_contexts"
        
        # If not found, try to find files ending with _fs_config / _file_contexts
        if not fs_config:
            candidates = list(config_dir.glob("*_fs_config"))
            if candidates: fs_config = candidates[0]
            
        if not file_contexts:
            candidates = list(config_dir.glob("*_file_contexts"))
            if candidates: file_contexts = candidates[0]
            
    # Also check root if not in config/
    if not fs_config:
        if (input_dir / "fs_config").exists(): fs_config = input_dir / "fs_config"
        else:
             candidates = list(input_dir.glob("*_fs_config"))
             if candidates: fs_config = candidates[0]

    if not file_contexts:
        if (input_dir / "file_contexts").exists(): file_contexts = input_dir / "file_contexts"
        else:
             candidates = list(input_dir.glob("*_file_contexts"))
             if candidates: file_contexts = candidates[0]
    
    # Check for Boot image files
    kernel = input_dir / "kernel"
    ramdisk = input_dir / "ramdisk"

    # Handle case where input_dir contains a subdirectory with the same name as the image (common extract behavior)
    # e.g. input_dir="product", inside is "product/" folder and "config/" folder
    # We want to pack "product/" folder using "config/" files.
    
    pack_source_dir = input_dir
    # Check for nested dir with same name as input_dir
    if (input_dir / input_dir.name).is_dir():
        pack_source_dir = input_dir / input_dir.name
        print(f"  - Detected nested source directory: {pack_source_dir.name}")
    # Also check for _a suffix (e.g. product_a inside product)
    elif (input_dir / f"{input_dir.name}_a").is_dir():
        pack_source_dir = input_dir / f"{input_dir.name}_a"
        print(f"  - Detected nested source directory: {pack_source_dir.name}")

    if fs_config and file_contexts and fs_config.exists() and file_contexts.exists():
        print(f"  - Detected Filesystem config. Packing as EROFS...")
        # Try to find mkfs.erofs
        mkfs_bin = get_tool("mkfs.erofs")
        
        # Determine mount point (guess from name)
        # e.g. product -> /product
        mount_point = f"/{input_dir.name}"
        if input_dir.name == "system": mount_point = "/"
        
        # Command: mkfs.erofs -z lz4hc -T 1230768000 --mount-point /... --fs-config-file ... --file-contexts ... out.img in_dir
        
        cmd = [
            mkfs_bin,
            "-z", "lz4hc",
            "-T", "1230768000",
            "--mount-point", mount_point,
            "--fs-config-file", str(fs_config),
            "--file-contexts", str(file_contexts),
            str(output_image),
            str(pack_source_dir)
        ]
        
        try:
            shell.run(cmd)
            print(f"  - Packed successfully: {output_image}")
        except Exception as e:
            print(f"  - Packing failed: {e}")
            print(f"  - Ensure 'mkfs.erofs' is installed or in bin/linux/x86_64.")

    elif kernel.exists() or ramdisk.exists():
        print(f"  - Detected Kernel/Ramdisk. Packing as Boot Image...")
        
        magiskboot_bin = get_tool("magiskboot")
        
        # Check if magiskboot is available
        try:
             shell.run([magiskboot_bin, "--version"], capture_output=True)
        except:
             print(f"  - [Error] magiskboot tool not found at {magiskboot_bin} or in PATH.")
             return

        print("  - [WARN] Boot image packing usually requires the original image for 'magiskboot repack'.")
        print("  - Assuming you want to run 'magiskboot repack' in the directory.")
        
        try:
            # We need to run magiskboot inside the directory
            # If we use the absolute path to magiskboot, it should work fine
            shell.run([magiskboot_bin, "repack", str(input_dir / "header")], cwd=input_dir)
            
            generated = input_dir / "new-boot.img"
            if generated.exists():
                shutil.move(generated, output_image)
                print(f"  - Packed successfully: {output_image}")
            else:
                print(f"  - Packing failed: 'new-boot.img' not found after repack.")
        except Exception as e:
            print(f"  - Magiskboot repack failed: {e}")
            
    else:
        print(f"  - Unknown directory structure. Cannot determine how to pack.")
        print(f"  - Expected 'fs_config' for EROFS or 'kernel' for Boot.")
        print(f"  - Contents of {input_dir}:")
        try:
            for item in list(input_dir.iterdir())[:10]:
                 print(f"    - {item.name}")
        except: pass

def process_super_images(chunks, output_root, shell):
    """Merge and unpack super images."""
    print(f"\n[Super Partition] Detected {len(chunks)} super image chunks. Merging...")
    super_img = output_root / "super.img"
    
    # Merge: simg2img chunk1 chunk2 ... out
    try:
        # simg2img takes input files and output file
        cmd = ["simg2img"] + [str(c) for c in chunks] + [str(super_img)]
        shell.run(cmd)
        print(f"[Super Partition] Merged to: {super_img}")
    except Exception as e:
        print(f"[Super Partition] Failed to merge: {e}")
        return []

    # Unpack
    print("[Super Partition] Unpacking logical partitions...")
    unpacked_files = []
    try:
        # Always use python implementation which handles sparse images better
        # Locate lpunpack.py relative to current script
        lpunpack_py = Path(__file__).resolve().parent.parent / "src" / "utils" / "lpunpack.py"
        cmd = [sys.executable, str(lpunpack_py), str(super_img), str(output_root)]
        shell.run(cmd)
        print("[Super Partition] Unpacking successful.")
        
        # Identify extracted partitions (assuming standard names)
        # We scan for .img files in output_root that were just created
        # For simplicity, we just look for common partition names
        common_parts = ["system", "vendor", "product", "odm", "system_ext", "mi_ext"]
        for p in output_root.glob("*.img"):
            if p.name != "super.img":
                # Skip empty images (often _b slots)
                if p.stat().st_size == 0:
                    print(f"[Super Partition] Skipping empty partition: {p.name}")
                    p.unlink()
                    continue
                    
                # Skip _b partitions if _a exists and has content
                if p.name.endswith("_b.img"):
                    a_name = p.name.replace("_b.img", "_a.img")
                    a_path = p.parent / a_name
                    if a_path.exists() and a_path.stat().st_size > 0:
                        print(f"[Super Partition] Skipping secondary slot partition: {p.name}")
                        p.unlink()
                        continue
                
                unpacked_files.append(p)
                
    except Exception as e:
        print(f"[Super Partition] lpunpack failed: {e}")
    
    # Clean up super.img to save space
    if super_img.exists():
        super_img.unlink()
        
    return unpacked_files

def main():
    parser = argparse.ArgumentParser(description="Extract/Pack Android Images (Boot/EROFS/Sparse)")
    parser.add_argument("input", nargs="+", help="Image files to extract, or directories to pack")
    parser.add_argument("--out", default="extracted_images", help="Output directory or file")
    parser.add_argument("--pack", action="store_true", help="Pack directory into image")
    args = parser.parse_args()

    shell = ShellRunner()
    
    if args.pack:
        for item in args.input:
            input_dir = Path(normalize_path(item)).resolve()
            
            out_arg = Path(normalize_path(args.out))
            output_path = None
            
            # Heuristic: if single input and out has extension (like .img), treat as file
            if len(args.input) == 1 and out_arg.suffix:
                 output_path = out_arg
                 if not output_path.parent.exists():
                     output_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                 # Treat as directory
                 if not out_arg.exists(): out_arg.mkdir(parents=True, exist_ok=True)
                 output_path = out_arg / f"{input_dir.name}.img"
            
            pack_image(input_dir, output_path, shell)
        return

    # Normalize output path
    out_root_str = normalize_path(args.out)
    out_root = Path(out_root_str).resolve()
    
    if not out_root.exists():
        out_root.mkdir(parents=True)
    
    print(f"Output Directory: {out_root}")

    # 1. Collect all input paths
    all_inputs = []
    
    for item in args.input:
        item = normalize_path(item)
        path_item = Path(item)
        
        if path_item.is_dir():
            print(f"Scanning directory: {path_item}")
            all_inputs.extend(path_item.glob("*.img"))
            all_inputs.extend(path_item.glob("super.img.*")) # Catch split files
            
        elif path_item.is_file():
            # Check if argument is a text file list
            if path_item.suffix == ".txt" or (path_item.suffix not in [".img", ".bin"] and "super.img" not in path_item.name):
                try:
                    with open(path_item, 'r', encoding='utf-8') as f:
                        lines = [line.strip() for line in f if line.strip()]
                        print(f"Loaded {len(lines)} paths from {path_item.name}")
                        for line in lines:
                            clean_path = line.strip('"').strip("'")
                            all_inputs.append(Path(normalize_path(clean_path)))
                except Exception as e:
                    print(f"Error reading list file {item}: {e}")
            else:
                all_inputs.append(path_item)

    # Deduplicate and Sort
    all_inputs = sorted(list(set(all_inputs)))

    # 2. Identify Super Image Chunks
    super_chunks = [p for p in all_inputs if "super.img." in p.name]
    regular_inputs = [p for p in all_inputs if p not in super_chunks]

    # 3. Process Super Images First
    if super_chunks:
        print(f"Found {len(super_chunks)} split super images.")
        unpacked = process_super_images(super_chunks, out_root, shell)
        # Add unpacked images to the processing queue
        if unpacked:
            print(f"Adding {len(unpacked)} unpacked partitions to extraction queue.")
            regular_inputs.extend(unpacked)

    # 4. Process All Images (Original + Unpacked)
    print(f"Starting extraction of {len(regular_inputs)} images...")
    for img in regular_inputs:
        # Skip super.img itself if it somehow got in
        if img.name == "super.img": continue
        
        extract_image(str(img), out_root, shell)

if __name__ == "__main__":
    main()
