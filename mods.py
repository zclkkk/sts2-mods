#!/usr/bin/env python3
"""Check upstream releases and install the pinned Slay the Spire 2 mod pack."""

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
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parent
DEFAULT_GAME = Path.home() / ".local/share/Steam/steamapps/common/Slay the Spire 2"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def version(tag):
    if not re.fullmatch(r"v?\d+(\.\d+){1,4}", tag):
        raise ValueError(f"不支持的版本标签 {tag!r}")
    return tuple(int(part) for part in tag.removeprefix("v").split("."))


def asset_name(mod, tag):
    version(tag)
    return mod["asset"].format(version=tag.removeprefix("v"))


def github_open(url, *, api=False):
    headers = {"User-Agent": "sts2-mods"}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return urlopen(Request(url, headers=headers), timeout=30)


def release_report(lock):
    rows = []
    for mod in lock["mods"]:
        repo = mod["repo"]
        try:
            with github_open(f"https://api.github.com/repos/{repo}/releases/latest", api=True) as response:
                release = json.load(response)
            newest = release["tag_name"]
            if version(newest) <= version(mod["tag"]):
                print(f'{mod["name"]}: {mod["tag"]}，无更高正式版')
                continue
        except (URLError, ValueError, KeyError) as error:
            raise ValueError(f'{mod["name"]} 查询失败：{error}') from error

        expected = asset_name(mod, newest)
        asset = next((item for item in release["assets"] if item["name"] == expected), None)
        digest = asset.get("digest") if asset else None
        note = f"`{expected}`"
        if not asset:
            note = f"需要手动选包（预期 {note}）"
        elif digest and re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            note += f"，SHA-256 `{digest[7:]}`"
        link = f"https://github.com/{repo}/releases/tag/{newest}"
        rows.append(f'| {mod["name"]} | {mod["tag"]} | [{newest}]({link}) | {note} |')
        print(f'{mod["name"]}: {mod["tag"]} → {newest}')

    if not rows:
        return ""
    header = (
        "# Mod 更新候选\n\n"
        f'当前游戏版本 **{lock["game_version"]}**（`{lock["steam_branch"]}`）。'
        "请按发行说明和本机测试确认兼容性，再更新锁定清单。\n\n"
        "| Mod | 已锁定 | 上游新版本 | 安装包 |\n"
        "| --- | --- | --- | --- |\n"
    )
    return header + "\n".join(rows) + "\n"


def manifests(folder, *, strict=True):
    result = {}
    for path in folder.glob("*.json"):
        try:
            data = read_json(path)
        except (ValueError, UnicodeError):
            if strict:
                raise
            continue
        if isinstance(data, dict) and "id" in data and "version" in data:
            mod_id = data["id"]
            if mod_id in result:
                raise ValueError(f"{folder} 中有重复的 Mod ID {mod_id}")
            result[mod_id] = data["version"]
    return result


def unpack(mod, package, destination):
    with package.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != mod["sha256"]:
            raise ValueError(f"{package.name} 的 SHA-256 与锁定清单不同")
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
    found = manifests(destination)
    if set(found) != set(mod["ids"]) or version(found[mod["ids"][0]]) != version(mod["tag"]):
        raise ValueError(f"{package.name} 的 Mod 清单与锁定清单不同")


def pack_files(lock, source, destination):
    for mod in lock["mods"]:
        name = asset_name(mod, mod["tag"])
        file = source / name
        if not file.is_file():
            raise ValueError(f"找不到 {file}")
        folder = destination / mod["ids"][0]
        folder.mkdir()
        unpack(mod, file, folder)
        print(f'已核对 {mod["name"]} {mod["tag"]}')


def fetch_packs(lock, destination):
    for mod in lock["mods"]:
        name = asset_name(mod, mod["tag"])
        repo = mod["repo"]
        tag = quote(mod["tag"], safe="")
        url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name, safe='')}"
        with github_open(url) as response, (destination / name).open("wb") as file:
            shutil.copyfileobj(response, file)


def preserve_unmanaged(lock, mods, destination):
    if not mods.exists():
        return
    managed = {mod_id for mod in lock["mods"] for mod_id in mod["ids"]}
    for path in mods.iterdir():
        found = set(manifests(path, strict=False)) if path.is_dir() and not path.is_symlink() else set()
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


def install(lock, game, packages, *, dry_run=False):
    installed_version = read_json(game / "release_info.json")["version"]
    if version(installed_version) != version(lock["game_version"]):
        raise ValueError(f'本机游戏版本 {installed_version} 与锁定版本 {lock["game_version"]} 不同')
    mods = game / "mods"
    backup = game / ".sts2-mods-previous"
    if mods.is_symlink() or (mods.exists() and not mods.is_dir()) or os.path.lexists(backup):
        raise ValueError(f"请检查安装目录 {mods} 和可能的备份 {backup}")

    # The staged tree and game directory must be on the same filesystem for rename.
    with tempfile.TemporaryDirectory(prefix=".sts2-mods-", dir=None if dry_run else game) as temp:
        staging = Path(temp)
        next_mods = staging / "mods"
        next_mods.mkdir()
        if packages is None:
            packages = staging / "packages"
            packages.mkdir()
            fetch_packs(lock, packages)
        pack_files(lock, packages, next_mods)
        preserve_unmanaged(lock, mods, next_mods)
        if dry_run:
            print(f"预检查通过：{mods}")
            return

        if read_json(game / "release_info.json")["version"] != installed_version:
            raise ValueError("准备安装时游戏版本发生了变化")
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
    check = commands.add_parser("check", help="检查作者的正式 Release")
    check.add_argument("--report", type=Path, help="输出 Markdown 更新报告")
    deploy = commands.add_parser("install", help="安装锁定版本，请先退出游戏")
    deploy.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    deploy.add_argument("--packages", type=Path, help="使用已下载的 ZIP")
    deploy.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    lock = read_json(ROOT / "mods.lock.json")
    try:
        if args.command == "check":
            report = release_report(lock)
            if args.report:
                args.report.write_text(report, encoding="utf-8")
        elif args.dry_run:
            install(lock, args.game_dir, args.packages, dry_run=True)
        else:
            directory = os.open(args.game_dir, os.O_DIRECTORY)
            try:
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
                install(lock, args.game_dir, args.packages)
            finally:
                os.close(directory)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
