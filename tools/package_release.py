"""Create two verified download assets; refuse personal/runtime state."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", default="out/releases/v0.2.0")
    parser.add_argument("--version", default="v0.2.0")
    parser.add_argument("--launch-fix", action="store_true", help="Also package entry-point replacements for existing installations")
    args = parser.parse_args()
    package = Path(args.package).resolve()
    output = Path(args.output).resolve()
    if package == output or package in output.parents:
        raise ValueError("Asset output must be outside the package")
    manifest = json.loads((package / "package-manifest.json").read_text(encoding="utf-8"))
    if manifest["scope"] != "windows-x64-portable-cpu":
        raise ValueError("Only the independently bundled runtime can be released")
    for item in manifest["files"]:
        if digest(package / item["file"]) != item["sha256"]:
            raise ValueError("Package manifest mismatch")
    files = sorted(path for path in package.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(package)
        if (set(relative.parts) & {"settings", "native-messaging", "browser-profile", "__pycache__"}
                or path.suffix.lower() in {".kdbx", ".log", ".pyc", ".pyo", ".lnk", ".pth", ".pt"}
                or path.name == "pyvenv.cfg"):
            raise ValueError("Personal state, old model names or nonportable runtime found")
    output.mkdir(parents=True, exist_ok=True)
    software = output / ("TKYP-Windows-x64-" + args.version + ".zip")
    extension = output / ("TKYP-Chrome-Edge-" + args.version + ".zip")
    with zipfile.ZipFile(software, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in files:
            archive.write(path, str(Path("TheyKnowYourPasswords") / path.relative_to(package)))
    extension_root = package / "extension-chromium"
    with zipfile.ZipFile(extension, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
        for path in sorted(extension_root.rglob("*")):
            if path.is_file():
                archive.write(path, str(Path("TheyKnowYourPasswords-Chromium") / path.relative_to(extension_root)))
        archive.write(package / "first-use.md", "TheyKnowYourPasswords-Chromium/INSTALL.md")
        archive.write(package / "licenses/browser-extension.LICENSE", "TheyKnowYourPasswords-Chromium/LICENSE")
    assets = [software, extension]
    if args.launch_fix:
        repair = output / ("TKYP-Launch-Fix-" + args.version + ".zip")
        with zipfile.ZipFile(repair, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for name in ("Launch.cmd", "启动软件.cmd", "Setup.cmd", "首次配置.cmd",
                         "CheckEnvironment.cmd", "start-demo.ps1", "first-use.md", "package-manifest.json"):
                archive.write(package / name, str(Path("TheyKnowYourPasswords") / name))
            archive.writestr("INSTALL.md", "# Startup repair\n\nExtract beside your existing TheyKnowYourPasswords folder and replace the eight included files.\nYour database, settings, models and software binaries are unchanged. Then run Launch.cmd.\n")
        assets.append(repair)
    checksums = []
    report = {"version": args.version, "assets": [], "scope": manifest["scope"]}
    for path in assets:
        if path.stat().st_size >= 2 * 1024**3:
            raise ValueError("GitHub Release asset exceeds 2 GiB")
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP CRC verification failed")
            count = len(archive.infolist())
        sha = digest(path)
        checksums.append(sha + "  " + path.name)
        report["assets"].append({"file": path.name, "bytes": path.stat().st_size,
                                "files": count, "sha256": sha, "crc": "OK"})
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
    (output / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
