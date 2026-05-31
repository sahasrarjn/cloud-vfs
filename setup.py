#!/usr/bin/env python3
from pathlib import Path

from setuptools import find_packages, setup

readme = Path("README.md").read_text(encoding="utf-8")

setup(
    name="cloud-vfs",
    version="0.5.5",
    description="Manual Azure blob virtual filesystem",
    long_description=readme,
    long_description_content_type="text/markdown",
    author="cloud-vfs contributors",
    url="https://github.com/sahasrarjn/cloud-vfs",
    license="MIT",
    packages=find_packages(),
    include_package_data=True,
    package_data={"cloud_vfs": ["bundled/**/*"]},
    python_requires=">=3.9",
    entry_points={"console_scripts": ["cloud-vfs=cloud_vfs.cli:main"]},
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
    ],
)
