"""Build the BlendLuxCore extension zips.

Both kinds of zip are made by Blender's own `extension build`, from a clean
copy of the committed files (`git archive HEAD`: commit first; `--worktree`
packages the working tree instead, to test uncommitted changes):

- The platform zips (`BlendLuxCore-<version>-<platform>.zip`), one per
  platform, with pyluxcore inside. Blender installs the wheels listed in the
  `wheels` entry of the extension manifest by itself, offline, when the
  extension is installed, so the user only has to pick the zip of their
  platform in "Install from Disk": no download and no settings file at the
  first start.

    python scripts/package_bundle.py --wheels <folder with pyluxcore wheels> \\
        --platform windows-x64 --out dist

  The wheel folder is what the LuxCore wheel build produces (`cibw-wheels-*`
  artifacts, the wheel release, or `make wheel`): any number of pyluxcore
  wheels. For each platform, the wheels of the wanted Python versions are
  picked (default: 3.11 of Blender 4.5 and 3.13 of Blender 5.x), together with
  the wheels pyluxcore requires (the CUDA NVRTC wheel), taken from the folder
  or downloaded with pip.

- The slim zip (`BlendLuxCore-<version>.zip`, `--slim`): no pyluxcore, for
  every platform; the addon downloads it at the first start (see
  luxloader/__init__.py) or takes the wheel of its settings file.

    python scripts/package_bundle.py --slim --out dist

`--tag Latest` names the zips after the tag instead of the manifest version
(the nightly builds). Needs `blender`: in the PATH, with `--blender <path>`,
or through BLENDER_ROOT (the folder of the executable); and `git` when the
source is a git checkout.
"""

import argparse
import os
import platform as platform_module
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Blender platform -> (substrings a wheel tag must contain, pip --platform)
PLATFORMS = {
    "windows-x64": (("win_amd64",), "win_amd64"),
    "linux-x64": (("manylinux", "x86_64"), "manylinux2014_x86_64"),
    "macos-arm64": (("macosx", "arm64"), "macosx_14_0_arm64"),
    "macos-x64": (("macosx", "x86_64"), "macosx_11_0_x86_64"),
}


def wheel_tags(wheel):
    """Return the (python, abi, platform) tags of a wheel filename."""
    parts = wheel.name[: -len(".whl")].split("-")
    return parts[-3], parts[-2], parts[-1]


def matches_platform(wheel, platform):
    """Whether a wheel runs on a platform (pure Python wheels run anywhere)."""
    plat_tag = wheel_tags(wheel)[2]
    if plat_tag == "any":
        return True
    return all(s in plat_tag for s in PLATFORMS[platform][0])


def requirements(wheel):
    """Return the (requirement, environment marker) pairs of a wheel."""
    with zipfile.ZipFile(wheel) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        text = zf.read(meta).decode("utf-8")
    reqs = []
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        spec, _, marker = line[len("Requires-Dist:"):].strip().partition(";")
        reqs.append((spec.strip(), marker.strip()))
    return reqs


def marker_applies(marker, platform):
    """Evaluate the only environment markers pyluxcore uses (`sys_platform`)."""
    if not marker:
        return True
    is_mac = platform.startswith("macos")
    if 'sys_platform != "darwin"' in marker:
        return not is_mac
    if 'sys_platform == "darwin"' in marker:
        return is_mac
    raise SystemExit(f"Unhandled environment marker: {marker!r}")


def download_wheel(spec, platform, pyminor, dest):
    """Fetch the wheel of a requirement for a platform with pip."""
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "download", "--no-deps", "--only-binary=:all:",
        "--platform", PLATFORMS[platform][1], "--python-version", f"3.{pyminor}",
        "-d", str(dest), spec,
    ]
    print("  " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def python_minor(wheel):
    """3.13 -> 13 for a cp313 wheel, None for the others."""
    m = re.fullmatch(r"cp3(\d+)", wheel_tags(wheel)[0])
    return int(m.group(1)) if m else None


def collect(wheel_dir, platform, pythons, cache):
    """Pick the wheels of a platform: pyluxcore for each Python, and its requirements."""
    chosen = {}
    for wheel in sorted(wheel_dir.glob("pyluxcore-*.whl")):
        if matches_platform(wheel, platform) and python_minor(wheel) in pythons:
            chosen[wheel.name] = wheel
    found = {python_minor(w) for w in chosen.values()}
    for minor in sorted(set(pythons) - found):
        print(f"  warning: no pyluxcore wheel for {platform}, Python 3.{minor}")
    if not chosen:
        return None

    for wheel in list(chosen.values()):
        for spec, marker in requirements(wheel):
            if not marker_applies(marker, platform):
                continue
            name = re.split(r"[=<>~! ]", spec, maxsplit=1)[0]
            prefix = name.replace("-", "_").lower() + "-"
            have = [
                w for w in [*wheel_dir.glob("*.whl"), *cache.glob("*.whl")]
                if w.name.lower().startswith(prefix) and matches_platform(w, platform)
            ]
            if not have:
                download_wheel(spec, platform, python_minor(wheel), cache)
                have = [
                    w for w in cache.glob("*.whl")
                    if w.name.lower().startswith(prefix) and matches_platform(w, platform)
                ]
            chosen[have[0].name] = have[0]
    return list(chosen.values())


def stage_source(repo, dest, worktree=False):
    """Export the committed files of the addon into dest (or the working tree)."""
    if (repo / ".git").exists() and not worktree:
        archive = dest.parent / "source.tar"
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), "HEAD"],
            check=True,
        )
        with tarfile.open(archive) as tar:
            tar.extractall(dest)
        archive.unlink()
    else:
        shutil.copytree(
            repo, dest, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", ".git", "wheels*", "build", "dist"),
        )
    # The runtime installer's leftovers must not ship
    shutil.rmtree(dest / "wheels", ignore_errors=True)
    shutil.rmtree(dest / "wheels_backup", ignore_errors=True)
    (dest / "pyluxcore_installation_info.txt").unlink(missing_ok=True)


def set_manifest(dest, platform, wheels):
    """Point the manifest at the bundled wheels (none for the slim zip) and,
    for a platform zip, at its platform. Return the manifest version."""
    manifest = dest / "blender_manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    wheel_list = ", ".join(f'"./wheels/{w.name}"' for w in wheels)
    text, n_wheels = re.subn(r"(?m)^wheels\s*=\s*\[.*\]", f"wheels = [{wheel_list}]", text)
    if n_wheels != 1:
        raise SystemExit("blender_manifest.toml: expected one `wheels` line")
    if platform:
        text, n_platforms = re.subn(
            r"(?m)^platforms\s*=\s*\[.*\]", f'platforms = ["{platform}"]', text
        )
        if n_platforms != 1:
            raise SystemExit("blender_manifest.toml: expected one `platforms` line")
    manifest.write_text(text, encoding="utf-8")
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1)


def find_blender(args):
    """The Blender executable: --blender, the PATH, or BLENDER_ROOT."""
    if args.blender != "blender" or shutil.which("blender"):
        return args.blender
    if root := os.environ.get("BLENDER_ROOT"):
        exe = "blender.exe" if platform_module.system() == "Windows" else "blender"
        candidate = Path(root) / exe
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(
        "blender not found: put it in the PATH, pass --blender, or set BLENDER_ROOT"
    )


def build_zip(src, args, name):
    """Run Blender's `extension build` on the staged source."""
    args.out.mkdir(parents=True, exist_ok=True)
    zip_path = args.out.resolve() / name
    subprocess.run(
        [
            args.blender, "--factory-startup", "--command", "extension", "build",
            "--source-dir", str(src), "--output-filepath", str(zip_path),
        ],
        check=True,
    )
    print(f"  -> {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")
    return zip_path


def build_platform(platform, args, cache):
    """A platform zip, with pyluxcore inside."""
    print(f"== {platform}")
    wheels = collect(args.wheels, platform, args.python, cache)
    if wheels is None:
        print(f"  no pyluxcore wheel for {platform}: skipped")
        return None

    with tempfile.TemporaryDirectory(prefix="blc-bundle-") as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        stage_source(REPO, src, args.worktree)
        (src / "wheels").mkdir()
        for wheel in wheels:
            shutil.copy2(wheel, src / "wheels" / wheel.name)
            print(f"  {wheel.name} ({wheel.stat().st_size / 1e6:.0f} MB)")
        version = set_manifest(src, platform, wheels)
        return build_zip(src, args, f"BlendLuxCore-{args.tag or version}-{platform}.zip")


def build_slim(args):
    """The slim zip: every platform, no pyluxcore."""
    print("== slim (no pyluxcore)")
    with tempfile.TemporaryDirectory(prefix="blc-bundle-") as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        stage_source(REPO, src, args.worktree)
        version = set_manifest(src, None, [])
        return build_zip(src, args, f"BlendLuxCore-{args.tag or version}.zip")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wheels", type=Path, help="folder with the pyluxcore wheels")
    parser.add_argument("--slim", action="store_true",
                        help="build the zip without pyluxcore (for every platform)")
    parser.add_argument(
        "--platform", nargs="+", default=["all"], choices=[*PLATFORMS, "all"],
        help="Blender platform(s) to build (default: all the platforms that have a wheel)",
    )
    parser.add_argument("--python", nargs="+", type=int, default=[11, 13], metavar="MINOR",
                        help="Python 3.x minors to bundle (default: 11 13)")
    parser.add_argument("--tag", help="name the zip after this tag instead of the version")
    parser.add_argument("--out", type=Path, default=REPO / "dist")
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--worktree", action="store_true",
                        help="package the working tree instead of `git archive HEAD` (to test)")
    args = parser.parse_args()
    if not args.slim and args.wheels is None:
        parser.error("--wheels <folder> (the platform zips) or --slim is required")
    args.blender = find_blender(args)

    if args.slim:
        build_slim(args)
        return

    platforms = list(PLATFORMS) if "all" in args.platform else args.platform
    cache = Path(tempfile.mkdtemp(prefix="blc-deps-"))
    try:
        built = [z for p in platforms if (z := build_platform(p, args, cache))]
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    if not built:
        raise SystemExit("Nothing built: no matching pyluxcore wheel in " + str(args.wheels))


if __name__ == "__main__":
    main()
