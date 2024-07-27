#!/usr/bin/env python3

import logging
import os
import platform
import re
import subprocess
import textwrap
import warnings
from pathlib import Path
from shutil import copy

import click
import pyperclip
from appdirs import user_config_dir
from daemonize import Daemonize
from picker import pick

# 设置剪切板为xclip
pyperclip.set_clipboard("xclip")

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO"))
log = logging.getLogger("inkscape-figures")


def inkscape(path):
    """
    用Inkscape打开指定的SVG文件
    """
    with warnings.catch_warnings():
        # leaving a subprocess running after interpreter exit raises a
        # warning in Python3.7+
        warnings.simplefilter("ignore", ResourceWarning)
        subprocess.Popen(["inkscape", str(path)])


def indent(text, indentation=0):
    """
    用指定数量的空格缩进文本
    """
    lines = text.split("\n")
    return "\n".join(" " * indentation + line for line in lines)


def beautify(name):
    """
    将文件名转换为漂亮的标题
    """
    return name.replace("_", " ").replace("-", " ").title()


def latex_template(name, title):
    return "\n".join(
        (
            r"\begin{figure}[ht]",
            r"    \centering",
            rf"    \incfig{{{name}}}",
            rf"    \caption{{{title}}}",
            rf"    \label{{fig:{name}}}",
            r"\end{figure}",
        )
    )


# From https://stackoverflow.com/a/67692
def import_file(name, path):
    """
    从文件导入模块
    """
    import importlib.util as util

    spec = util.spec_from_file_location(name, path)
    if spec is None:
        raise ImportError("Could not import file")
    if spec.loader is None:
        raise ImportError("Could not import file")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load user config
# linux中返回$HOME/.config/inkscape-figures
user_dir = Path(user_config_dir("inkscape-figures", "Castel"))

if not user_dir.is_dir():  # 如果目录不存在则创建
    user_dir.mkdir()

roots_file = user_dir / "roots"  # $HOME/.config/inkscape-figures/roots
template = user_dir / "template.svg"  # $HOME/.config/inkscape-figures/template.svg
config = user_dir / "config.py"  # $HOME/.config/inkscape-figures/config.py

if not roots_file.is_file():  # 如果文件不存在则创建
    roots_file.touch()

if not template.is_file():  # 如果文件不存在则从模板文件复制
    source = str(Path(__file__).parent / "template.svg")
    destination = str(template)
    copy(source, destination)

if config.exists():  # 如果配置文件存在则导入模块
    config_module = import_file("config", config)
    latex_template = config_module.latex_template


def add_root(path):
    """
    添加一个根目录
    """
    path = str(path)
    roots = get_roots()
    if path in roots:
        return None

    roots.append(path)
    roots_file.write_text("\n".join(roots))


def get_roots():
    """
    获取所有根目录
    """
    return [root for root in roots_file.read_text().split("\n") if root != ""]


@click.group()  # 创建一个命令组
def cli():
    pass


@cli.command()  # 创建一个命令
@click.option("--daemon/--no-daemon", default=True)  # 创建一个选项
def watch(daemon):
    """
    创建一个守护进程，用于监视图像文件的更改
    """
    if platform.system() == "Linux":
        watcher_cmd = watch_daemon_inotify
    else:
        watcher_cmd = watch_daemon_fswatch

    if daemon:
        daemon = Daemonize(
            app="inkscape-figures", pid="/tmp/inkscape-figures.pid", action=watcher_cmd
        )
        daemon.start()
        log.info("Watching figures.")
    else:
        log.info("Watching figures.")
        watcher_cmd()


def maybe_recompile_figure(filepath):
    """
    如果svg发生更改，则重新编译图形
    即创建一个包含图片的pdf文件和包含文字的latex文件
    """
    filepath = Path(filepath)
    # A file has changed
    if filepath.suffix != ".svg":
        log.debug("File has changed, but is nog an svg {}".format(filepath.suffix))
        return

    log.info("Recompiling %s", filepath)

    # svg文件同路径创建一个pdf文件
    pdf_path = filepath.parent / (filepath.stem + ".pdf")
    name = filepath.stem

    inkscape_version = subprocess.check_output(
        ["inkscape", "--version"], universal_newlines=True
    )
    log.debug(inkscape_version)

    # Convert
    # - 'Inkscape 0.92.4 (unknown)' to [0, 92, 4]
    # - 'Inkscape 1.1-dev (3a9df5bcce, 2020-03-18)' to [1, 1]
    # - 'Inkscape 1.0rc1' to [1, 0]
    inkscape_version = re.findall(r"[0-9.]+", inkscape_version)[0]
    inkscape_version_number = [int(part) for part in inkscape_version.split(".")]

    # Right-pad the array with zeros (so [1, 1] becomes [1, 1, 0])
    inkscape_version_number = inkscape_version_number + [0] * (
        3 - len(inkscape_version_number)
    )

    # Tuple comparison is like version comparison
    if inkscape_version_number < [1, 0, 0]:
        command = [
            "inkscape",
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-pdf",
            pdf_path,
            # "--export-latex",
            filepath,
        ]
    else:
        command = [
            "inkscape",
            filepath,
            "--export-area-page",
            "--export-dpi",
            "300",
            "--export-type=pdf",
            # "--export-latex",
            "--export-filename",
            pdf_path,
        ]

    log.debug("Running command:")
    log.debug(textwrap.indent(" ".join(str(e) for e in command), "    "))

    # Recompile the svg file
    completed_process = subprocess.run(command)

    if completed_process.returncode != 0:
        log.error("Return code %s", completed_process.returncode)
    else:
        log.debug("Command succeeded")

    # Copy the LaTeX code to include the file to the clipboard
    template = latex_template(name, beautify(name))
    pyperclip.copy(template)
    log.debug("Copying LaTeX template:")
    log.debug(textwrap.indent(template, "    "))


def watch_daemon_inotify():
    """
    使用inotify监控文件变化
    """
    import inotify.adapters
    from inotify.constants import IN_CLOSE_WRITE

    while True:
        roots = get_roots()

        # Watch the file with contains the paths to watch
        # When this file changes, we update the watches.
        i = inotify.adapters.Inotify()
        i.add_watch(str(roots_file), mask=IN_CLOSE_WRITE)

        # Watch the actual figure directories
        log.info("Watching directories: " + ", ".join(get_roots()))
        for root in roots:
            try:
                i.add_watch(root, mask=IN_CLOSE_WRITE)
            except Exception:
                log.debug("Could not add root %s", root)

        for event in i.event_gen(yield_nones=False):
            if event is None:
                continue
            (_, type_names, path, filename) = event

            # If the file containing figure roots has changes, update the
            # watches
            if path == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                for root in roots:
                    try:
                        i.remove_watch(root)
                        log.debug("Removed root %s", root)
                    except Exception:
                        log.debug("Could not remove root %s", root)
                # Break out of the loop, setting up new watches.
                break

            # A file has changed
            path = Path(path) / filename
            maybe_recompile_figure(path)


def watch_daemon_fswatch():
    while True:
        roots = get_roots()
        log.info("Watching directories: " + ", ".join(roots))
        # Watch the figures directories, as weel as the config directory
        # containing the roots file (file containing the figures to the figure
        # directories to watch). If the latter changes, restart the watches.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            p = subprocess.Popen(
                ["fswatch", *roots, str(user_dir)],
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )

        while True:
            if p.stdout is None:
                raise Exception("fswatch has terminated")
            filepath = p.stdout.readline().strip()

            # If the file containing figure roots has changes, update the
            # watches
            if filepath == str(roots_file):
                log.info("The roots file has been updated. Updating watches.")
                p.terminate()
                log.debug("Removed main watch %s")
                break
            maybe_recompile_figure(filepath)


@cli.command()
@click.argument("title")
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=False, file_okay=False, dir_okay=True),
)
def create(title, root):
    """
    创建一个新的图形

    第一个参数是图形的标题
    第二个参数是图形目录

    """
    title = title.strip()  # 去除首尾空格
    file_name = title.replace(" ", "-").lower() + ".svg"  # 将标题转换为文件名
    figures = Path(root).absolute()
    if not figures.exists():  # 如果目录不存在则创建
        figures.mkdir()

    figure_path = figures / file_name

    # 如果文件已经存在则在文件名后加上'2'
    if figure_path.exists():
        print(title + " 2")
        return

    copy(str(template), str(figure_path))  # 复制模板文件到目标文件
    add_root(figures)  # 添加根目录
    inkscape(figure_path)  # 用Inkscape打开文件

    # Print the code for including the figure to stdout.
    # Copy the indentation of the input.
    leading_spaces = len(title) - len(title.lstrip())
    print(indent(latex_template(figure_path.stem, title), indentation=leading_spaces))


@cli.command()
@click.argument(
    "root",
    default=os.getcwd(),
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
def edit(root):
    """
    编辑一个图形

    第一个参数是图形目录
    """

    figures = Path(root).absolute()

    # 搜索目录下的所有svg文件并按修改时间排序
    files = figures.glob("*.svg")
    files = sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)

    # 用gui picker打开一个选择对话框，如rofi
    names = [beautify(f.stem) for f in files]
    _, index, selected = pick(names)
    if selected:
        path = files[index]
        add_root(figures)
        inkscape(path)

        # Copy the LaTeX code to include the file to the clipboard
        template = latex_template(path.stem, beautify(path.stem))
        pyperclip.copy(template)
        log.debug("Copying LaTeX template:")
        log.debug(textwrap.indent(template, "    "))


if __name__ == "__main__":
    cli()
