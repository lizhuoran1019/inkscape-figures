import pathlib
import platform
from distutils.spawn import find_executable

from setuptools import find_packages, setup


def readme():
    with open("README.md") as f:
        return f.read()


dependencies = ["pyperclip", "click", "appdirs", "daemonize"]
if find_executable("fswatch") is None:
    if platform.system() == "Linux":
        dependencies.append("inotify")
    else:
        raise ValueError(
            "inkscape-figures needs fswatch to run on MacOS. You "
            "can install it using `brew install fswatch`"
        )

setup(
    name="inkscape-figures",
    version="1.0.8",
    description="Script for managing inkscape figures",
    long_description=readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/lizhuoran1019/inkscape-figures",
    author="lzr",
    author_email="3200348589@qq.com",
    license="MIT",
    classifiers=[
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
    ],
    packages=["inkscapefigures"],
    scripts=["bin/inkscape-figures"],
    install_requires=dependencies,
    include_package_data=True,
)
