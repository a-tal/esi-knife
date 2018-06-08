"""ESI knife."""


import io
import os
import re
from setuptools import setup
from setuptools import find_packages


def find_version(filename):
    """Uses re to pull out the assigned value to __version__ in filename."""

    with io.open(filename, "r", encoding="utf-8") as version_file:
        version_match = re.search(r'^__version__ = [\'"]([^\'"]*)[\'"]',
                                  version_file.read(), re.M)
    if version_match:
        return version_match.group(1)
    return "0.0-version-unknown"


if os.path.isfile("README.md"):
    with io.open("README.md", encoding="utf-8") as opendescr:
        long_description = opendescr.read()
else:
    long_description = __doc__


setup(
    name="esi-knife",
    version=find_version("esi_knife/__init__.py"),
    description="ESI knife.",
    author="Adam Talsma",
    author_email="adam@talsma.ca",
    url="https://esi.a-t.al/",
    entry_points={
        "console_scripts": [
            "knife = esi_knife.cli:main",
            "knife-worker = esi_knife.worker:main",
        ],
    },
    install_requires=[
        "Flask>=0.12.0",
        "requests>=2.9.0",
        "Flask-Cache-Cassandra",
        "gevent",
        "redis",
        "docopt",
        "jsonderef",
        "ujson",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Web Environment",
        "Framework :: Flask",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "License :: OSI Approved :: MIT License",
    ],
    extras_require={
        "deploy": ["gunicorn"],
        ":python_version < '3'": ["enum34", "futures"],
    },
    include_package_data=True,
    zip_safe=False,
    package_data={
        "esi-knife": [
            os.path.join("esi_knife", "templates", f) for f in
            os.listdir(os.path.join("esi_knife", "templates"))
        ],
    },
    packages=find_packages(),
    long_description=long_description,
)
