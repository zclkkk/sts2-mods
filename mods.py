#!/usr/bin/env python3
"""Build and install Slay the Spire 2 mod packs."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parent
DEFAULT_GAME = Path.home() / ".local/share/Steam/steamapps/common/Slay the Spire 2"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def github_open(url, *, api=False):
    headers = {"User-Agent": "sts2-mods"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return urlopen(Request(url, headers=headers), timeout=30)


def latest_mods(config):
    choices = []
    for mod in config["mods"]:
        try:
            with github_open(f'https://api.github.com/repos/{mod["repo"]}/releases/latest', api=True) as response:
                release = json.load(response)
            tag = release["tag_name"]
            name = mod["asset"].format(version=tag.removeprefix("v"))
            asset = next((item for item in release["assets"] if item["name"] == name), None)
            if asset is None:
                raise ValueError(f"{tag} 缺少预期的安装包 {name}")
            digest = asset.get("digest")
            if digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise ValueError(f"{name} 的 GitHub 校验值无效")
            choices.append({**mod, "tag": tag, "asset": name, "sha256": digest[7:] if digest else None})
            print(f'{mod["name"]}: {tag} ({name})')
        except (URLError, ValueError, KeyError) as error:
            raise ValueError(f'{mod["name"]} 查询失败：{error}') from error
    return choices


def installed_ids(folder):
    result = set()
    for path in folder.glob("*.json"):
        try:
            data = read_json(path)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str) and "version" in data:
            result.add(data["id"])
    return result


def unpack(mod, package, destination):
    with package.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if mod["sha256"] and mod["sha256"] != digest:
        raise ValueError(f"{package.name} 的 SHA-256 与预期不同")
    with ZipFile(package) as archive:
        seen = set()
        if sum(entry.file_size for entry in archive.infolist()) > 256 * 1024 * 1024:
            raise ValueError(f"{package.name} 解压后超过 256 MiB")
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            file_type = stat.S_IFMT(entry.external_attr >> 16)
            if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or path in seen or file_type not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError(f"{package.name} 的归档条目无效：{entry.filename!r}")
            seen.add(path)
        archive.extractall(destination)
    return digest


def pack_files(mods, source, destination):
    for mod in mods:
        file = source / mod["asset"]
        if not file.is_file():
            raise ValueError(f"找不到 {file}")
        folder = destination / mod["ids"][0]
        folder.mkdir()
        mod["sha256"] = unpack(mod, file, folder)
        print(f'已核对 {mod["name"]} {mod["tag"]}')


def fetch_packs(mods, destination):
    for mod in mods:
        name = mod["asset"]
        repo = mod["repo"]
        tag = quote(mod["tag"], safe="")
        url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name, safe='')}"
        with github_open(url) as response, (destination / name).open("wb") as file:
            shutil.copyfileobj(response, file)


def build_bundle(config, output):
    output.mkdir(parents=True, exist_ok=True)
    selected = latest_mods(config)
    with tempfile.TemporaryDirectory(prefix="sts2-bundle-") as temp:
        work = Path(temp)
        packages = work / "packages"
        mods = work / "mods"
        packages.mkdir()
        mods.mkdir()
        fetch_packs(selected, packages)
        pack_files(selected, packages, mods)

        sources = [
            {key: mod[key] for key in ("name", "repo", "tag", "asset", "sha256")}
            for mod in selected
        ]
        manifest = {
            "format": 2,
            "steam_branch": config["steam_branch"],
            "mods": sources,
        }
        metadata = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        tag = f'mods-{hashlib.sha256(metadata).hexdigest()[:16]}'
        bundle_name = "sts2-mods.zip"
        (work / "mods.bundle.json").write_bytes(metadata)
        with ZipFile(work / bundle_name, "w") as archive:
            files = sorted(path for path in mods.rglob("*") if path.is_file())
            files.append(work / "mods.bundle.json")
            for path in files:
                entry = ZipInfo(path.relative_to(work).as_posix())
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, path.read_bytes(), compress_type=ZIP_DEFLATED)
        with (work / bundle_name).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        (work / f"{bundle_name}.sha256").write_text(f"{digest}  {bundle_name}\n")
        for name in (bundle_name, f"{bundle_name}.sha256", "mods.bundle.json"):
            shutil.move(work / name, output / name)

    notes = [
        f'Steam `{config["steam_branch"]}` 使用的 Mod 合集，收录各作者的最新正式 Release。',
        "",
        f"解压 `{bundle_name}`，把其中的 `mods/` 放进游戏安装目录。",
        "",
        "### 收录版本",
        "",
    ]
    for mod in sources:
        url = f'https://github.com/{mod["repo"]}/releases/tag/{mod["tag"]}'
        notes.append(f'- {mod["name"]} [{mod["tag"]}]({url})')
    (output / "release-notes.md").write_text("\n".join(notes) + "\n")
    (output / "release-tag.txt").write_text(tag + "\n")
    print(f"已生成 {bundle_name}（{tag}）")


def preserve_unmanaged(config, mods, destination):
    if not mods.exists():
        return
    managed = {mod_id for mod in config["mods"] for mod_id in mod["ids"]}
    for path in mods.iterdir():
        found = installed_ids(path) if path.is_dir() and not path.is_symlink() else set()
        if found & managed:
            if found - managed:
                raise ValueError(f"{path} 同时包含其他 Mod，请先拆分目录")
            continue
        target = destination / path.name
        if target.exists():
            raise ValueError(f"已有目录与安装目标重名：{path}")
        if path.is_dir() and not path.is_symlink():
            shutil.copytree(path, target, symlinks=True)
        else:
            shutil.copy2(path, target, follow_symlinks=False)


def install(config, game, packages, *, dry_run=False):
    if not (game / "release_info.json").is_file():
        raise ValueError(f"找不到游戏安装目录：{game}")
    mods = game / "mods"
    backup = game / ".sts2-mods-previous"
    if mods.is_symlink() or (mods.exists() and not mods.is_dir()) or os.path.lexists(backup):
        raise ValueError(f"请检查安装目录 {mods} 和可能的备份 {backup}")

    selected = latest_mods(config)
    # The staged tree and game directory must be on the same filesystem for rename.
    with tempfile.TemporaryDirectory(prefix=".sts2-mods-", dir=None if dry_run else game) as temp:
        staging = Path(temp)
        next_mods = staging / "mods"
        next_mods.mkdir()
        if packages is None:
            packages = staging / "packages"
            packages.mkdir()
            fetch_packs(selected, packages)
        pack_files(selected, packages, next_mods)
        preserve_unmanaged(config, mods, next_mods)
        if dry_run:
            print(f"预检查通过：{mods}")
            return

        if mods.exists():
            mods.rename(backup)
        try:
            next_mods.rename(mods)
        except BaseException:
            if backup.exists():
                backup.rename(mods)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    print(f"已安装到 {mods}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="显示将打包的作者正式 Release")
    bundle = commands.add_parser("bundle", help="打包作者最新的正式 Release")
    bundle.add_argument("--output", type=Path, required=True)
    deploy = commands.add_parser("install", help="安装作者最新的正式 Release，请先退出游戏")
    deploy.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    deploy.add_argument("--packages", type=Path, help="使用已下载的 ZIP")
    deploy.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        config = read_json(ROOT / "mods.json")
        if args.command == "check":
            latest_mods(config)
        elif args.command == "bundle":
            build_bundle(config, args.output)
        elif args.dry_run:
            install(config, args.game_dir, args.packages, dry_run=True)
        else:
            directory = os.open(args.game_dir, os.O_DIRECTORY)
            try:
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
                install(config, args.game_dir, args.packages)
            finally:
                os.close(directory)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
