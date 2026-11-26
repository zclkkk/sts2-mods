#!/usr/bin/env python3
"""Build the Slay the Spire 2 release ZIP."""

import argparse
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


def stage_mods(mods, work, destination):
    for mod in mods:
        name = mod["asset"]
        repo = mod["repo"]
        tag = quote(mod["tag"], safe="")
        url = f"https://github.com/{repo}/releases/download/{tag}/{quote(name, safe='')}"
        package = work / name
        with github_open(url) as response, package.open("wb") as file:
            shutil.copyfileobj(response, file)
        folder = destination / mod["folder"]
        folder.mkdir()
        mod["sha256"] = unpack(mod, package, folder)
        print(f'已核对 {mod["name"]} {mod["tag"]}')


def build_bundle(config, output):
    output.mkdir(parents=True, exist_ok=True)
    selected = latest_mods(config)
    with tempfile.TemporaryDirectory(prefix="sts2-bundle-") as temp:
        work = Path(temp)
        mods = work / "mods"
        mods.mkdir()
        stage_mods(selected, work, mods)

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle", help="打包作者最新的正式 Release")
    bundle.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = read_json(ROOT / "mods.json")
        build_bundle(config, args.output)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
