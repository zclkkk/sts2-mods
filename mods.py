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


def github_json(url):
    with github_open(url, api=True) as response:
        return json.load(response)


def latest_release(mod, game_version):
    api = f'https://api.github.com/repos/{mod["repo"]}/releases'
    if not mod.get("game_versioned_tags"):
        return github_json(f"{api}/latest")

    # Undo And Restart publishes separate releases for each exact game version.
    pattern = rf"v?{re.escape(game_version)}\.\d+"
    page = 1
    while releases := github_json(f"{api}?per_page=100&page={page}"):
        matches = [
            release for release in releases
            if not release["draft"] and not release["prerelease"]
            and re.fullmatch(pattern, release["tag_name"])
        ]
        if matches:
            return max(matches, key=lambda release: version(release["tag_name"]))
        page += 1
    raise ValueError(f'未找到 {mod["name"]} 对应游戏 {game_version} 的正式版')


def latest_mods(lock):
    choices = []
    for mod in lock["mods"]:
        try:
            release = latest_release(mod, lock["game_version"])
            tag = release["tag_name"]
            name = asset_name(mod, tag)
            asset = next((item for item in release["assets"] if item["name"] == name), None)
            if asset is None:
                raise ValueError(f"{tag} 缺少预期的安装包 {name}")
            digest = asset.get("digest")
            if digest and not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
                raise ValueError(f"{name} 的 GitHub 校验值无效")
            choices.append({**mod, "tag": tag, "sha256": digest[7:] if digest else None})
            print(f'{mod["name"]}: {tag} ({name})')
        except (URLError, ValueError, KeyError) as error:
            raise ValueError(f'{mod["name"]} 查询失败：{error}') from error
    return choices


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
            result[mod_id] = data
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
    found = manifests(destination)
    if set(found) != set(mod["ids"]) or version(found[mod["ids"][0]]["version"]) != version(mod["tag"]):
        raise ValueError(f"{package.name} 的 Mod 清单与预期不同")
    return digest, found


def pack_files(mods, source, destination):
    details = {}
    for mod in mods:
        name = asset_name(mod, mod["tag"])
        file = source / name
        if not file.is_file():
            raise ValueError(f"找不到 {file}")
        folder = destination / mod["ids"][0]
        folder.mkdir()
        digest, found = unpack(mod, file, folder)
        details.update(found)
        mod["sha256"] = digest
        print(f'已核对 {mod["name"]} {mod["tag"]}')
    return details


def fetch_packs(mods, destination):
    for mod in mods:
        name = asset_name(mod, mod["tag"])
        repo = mod["repo"]
        tag = quote(mod["tag"], safe="")
        url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name, safe='')}"
        with github_open(url) as response, (destination / name).open("wb") as file:
            shutil.copyfileobj(response, file)


def validate_bundle(game_version, found, mods):
    target = version(game_version)
    for mod_id, manifest in found.items():
        minimum = manifest.get("min_game_version")
        if minimum and version(minimum) > target:
            raise ValueError(f"{mod_id} 需要游戏 {minimum} 或更新版本")
        for dependency in manifest.get("dependencies") or []:
            dep_id = dependency if isinstance(dependency, str) else dependency["id"]
            if dep_id not in found:
                raise ValueError(f"{mod_id} 缺少依赖 {dep_id}")
            minimum = dependency.get("min_version") if isinstance(dependency, dict) else None
            if minimum and version(found[dep_id]["version"]) < version(minimum):
                raise ValueError(f"{mod_id} 需要 {dep_id} {minimum} 或更新版本")
    variants = mods / "STS2-RitsuLib" / "compat"
    if variants.is_dir():
        versions = [version(path.name) for path in variants.iterdir() if path.is_dir()]
        if not any(candidate[:2] == target[:2] and candidate <= target for candidate in versions):
            raise ValueError(f"RitsuLib 变体包缺少与游戏 {game_version} 匹配的组件")


def build_bundle(lock, output):
    output.mkdir(parents=True, exist_ok=True)
    game_version = lock["game_version"]
    selected = latest_mods(lock)
    with tempfile.TemporaryDirectory(prefix="sts2-bundle-") as temp:
        work = Path(temp)
        packages = work / "packages"
        mods = work / "mods"
        packages.mkdir()
        mods.mkdir()
        fetch_packs(selected, packages)
        found = pack_files(selected, packages, mods)
        validate_bundle(game_version, found, mods)

        sources = [
            {
                "name": mod["name"],
                "repo": mod["repo"],
                "tag": mod["tag"],
                "asset": asset_name(mod, mod["tag"]),
                "sha256": mod["sha256"],
            }
            for mod in selected
        ]
        manifest = {
            "format": 1,
            "game_version": game_version,
            "steam_branch": lock["steam_branch"],
            "mods": sources,
        }
        metadata = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        tag = f'mods-{game_version}-{hashlib.sha256(metadata).hexdigest()[:16]}'
        bundle_name = f"sts2-mods-{game_version}.zip"
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
        f'游戏版本 **{game_version}**（`{lock["steam_branch"]}`）。',
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
            fetch_packs(lock["mods"], packages)
        pack_files(lock["mods"], packages, next_mods)
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
    commands.add_parser("check", help="显示将打包的作者正式 Release")
    bundle = commands.add_parser("bundle", help="打包作者最新的正式 Release")
    bundle.add_argument("--output", type=Path, required=True)
    deploy = commands.add_parser("install", help="安装锁定版本，请先退出游戏")
    deploy.add_argument("--game-dir", type=Path, default=DEFAULT_GAME)
    deploy.add_argument("--packages", type=Path, help="使用已下载的 ZIP")
    deploy.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    lock = read_json(ROOT / "mods.lock.json")
    try:
        if args.command == "check":
            latest_mods(lock)
        elif args.command == "bundle":
            build_bundle(lock, args.output)
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
